import io
import ipaddress
import re
import socket
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse, urlunparse

import httpx
from bs4 import BeautifulSoup
import pdfplumber
from docx import Document
import markdown

from app.config import get_settings
from app.utils.helpers import clean_text
from app.utils.logging import logger

settings = get_settings()

BLOCKED_NETWORKS = [
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.0.0.0/24"),
    ipaddress.ip_network("192.0.2.0/24"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("198.18.0.0/15"),
    ipaddress.ip_network("198.51.100.0/24"),
    ipaddress.ip_network("203.0.113.0/24"),
    ipaddress.ip_network("224.0.0.0/4"),
    ipaddress.ip_network("240.0.0.0/4"),
    ipaddress.ip_network("255.255.255.255/32"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
    ipaddress.ip_network("ff00::/8"),
]

BLOCKED_HOSTNAMES = {
    "metadata.google.internal",
    "metadata",
    "169.254.169.254",
    "metadata.tencentyun.com",
    "metadata1.tencentyun.com",
    "metadata2.tencentyun.com",
    "metadata3.tencentyun.com",
    "metadata4.tencentyun.com",
    "metadata5.tencentyun.com",
}

ALLOWED_SCHEMES = {"http", "https"}

ALLOWED_PORTS = {80, 8080, 443, 8443}

MAX_REDIRECTS = 3


class InputExtractError(Exception):
    pass


class SSRFBlockedError(InputExtractError):
    pass


class InputService:
    @staticmethod
    def _is_ip_blocked(ip_str: str) -> bool:
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            return True
        for net in BLOCKED_NETWORKS:
            if ip in net:
                return True
        return False

    @staticmethod
    def _resolve_host(hostname: str) -> list[str]:
        try:
            infos = socket.getaddrinfo(hostname, None)
            ips = []
            for info in infos:
                ips.append(info[4][0])
            return ips
        except socket.gaierror:
            return []

    @staticmethod
    def _validate_url(url: str) -> tuple[str, str]:
        parsed = urlparse(url)

        if parsed.scheme not in ALLOWED_SCHEMES:
            raise SSRFBlockedError(
                f"URL scheme not allowed: {parsed.scheme}. Only http/https permitted."
            )

        if not parsed.hostname:
            raise SSRFBlockedError("URL has no valid hostname")

        hostname = parsed.hostname.lower()

        if hostname in BLOCKED_HOSTNAMES:
            raise SSRFBlockedError(f"Hostname blocked: {hostname}")

        if parsed.port is not None and parsed.port not in ALLOWED_PORTS:
            raise SSRFBlockedError(f"Port not allowed: {parsed.port}")

        try:
            ipaddress.ip_address(hostname)
            if InputService._is_ip_blocked(hostname):
                raise SSRFBlockedError(f"IP address blocked: {hostname}")
        except ValueError:
            ips = InputService._resolve_host(hostname)
            if not ips:
                raise SSRFBlockedError(f"Cannot resolve hostname: {hostname}")
            for ip in ips:
                if InputService._is_ip_blocked(ip):
                    raise SSRFBlockedError(
                        f"Resolved IP {ip} for hostname {hostname} is in blocked range"
                    )

        safe_url = urlunparse((
            parsed.scheme,
            parsed.hostname + (f":{parsed.port}" if parsed.port else ""),
            parsed.path or "/",
            parsed.params,
            parsed.query,
            "",
        ))

        return safe_url, hostname

    @staticmethod
    def _extract_pdf(content: bytes) -> str:
        try:
            text_parts = []
            with pdfplumber.open(io.BytesIO(content)) as pdf:
                for page in pdf.pages:
                    page_text = page.extract_text() or ""
                    text_parts.append(page_text)
            return clean_text("\n".join(text_parts))
        except Exception as e:
            logger.error(f"PDF extract error: {e}")
            raise InputExtractError(f"Failed to extract PDF: {e}")

    @staticmethod
    def _extract_docx(content: bytes) -> str:
        try:
            doc = Document(io.BytesIO(content))
            paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
            return clean_text("\n".join(paragraphs))
        except Exception as e:
            logger.error(f"DOCX extract error: {e}")
            raise InputExtractError(f"Failed to extract DOCX: {e}")

    @staticmethod
    def _extract_markdown(content: bytes) -> str:
        try:
            md_text = content.decode("utf-8", errors="ignore")
            html = markdown.markdown(md_text, extensions=["extra"])
            soup = BeautifulSoup(html, "html.parser")
            text = soup.get_text(separator="\n")
            return clean_text(text)
        except Exception as e:
            logger.error(f"Markdown extract error: {e}")
            raise InputExtractError(f"Failed to extract Markdown: {e}")

    @staticmethod
    def _extract_plain_text(content: bytes) -> str:
        try:
            text = content.decode("utf-8", errors="ignore")
            return clean_text(text)
        except Exception as e:
            logger.error(f"Plain text extract error: {e}")
            raise InputExtractError(f"Failed to extract text: {e}")

    @staticmethod
    def extract_from_bytes(content: bytes, filename: str) -> str:
        suffix = Path(filename).suffix.lower()
        if suffix == ".pdf":
            return InputService._extract_pdf(content)
        elif suffix == ".docx":
            return InputService._extract_docx(content)
        elif suffix in (".md", ".markdown"):
            return InputService._extract_markdown(content)
        elif suffix in (".txt", ".text", ""):
            return InputService._extract_plain_text(content)
        else:
            raise InputExtractError(f"Unsupported file format: {suffix}")

    @staticmethod
    def extract_from_filepath(filepath: str | Path) -> tuple[str, str]:
        p = Path(filepath)
        if not p.exists():
            raise InputExtractError(f"File not found: {filepath}")
        content = p.read_bytes()
        text = InputService.extract_from_bytes(content, p.name)
        return text, p.name

    @staticmethod
    async def _safe_request(
        url: str,
        timeout: int = 30,
        redirect_count: int = 0,
    ) -> httpx.Response:
        if redirect_count > MAX_REDIRECTS:
            raise SSRFBlockedError(f"Too many redirects (max {MAX_REDIRECTS})")

        safe_url, _ = InputService._validate_url(url)

        logger.info(f"Fetching URL (redirect level {redirect_count}): {safe_url}")

        try:
            async with httpx.AsyncClient(
                timeout=timeout,
                follow_redirects=False,
            ) as client:
                resp = await client.get(
                    safe_url,
                    headers={
                        "User-Agent": "Mozilla/5.0 (compatible; PodcastBot/1.0)"
                    },
                )

                if resp.status_code in (301, 302, 303, 307, 308):
                    location = resp.headers.get("Location")
                    if not location:
                        resp.raise_for_status()
                        return resp

                    if location.startswith("/"):
                        current = urlparse(safe_url)
                        redirect_url = urlunparse((
                            current.scheme,
                            current.netloc,
                            location,
                            "",
                            "",
                            "",
                        ))
                    elif location.startswith("http://") or location.startswith("https://"):
                        redirect_url = location
                    else:
                        raise SSRFBlockedError(f"Redirect location blocked: {location}")

                    return await InputService._safe_request(
                        redirect_url, timeout, redirect_count + 1
                    )

                resp.raise_for_status()
                return resp

        except httpx.HTTPError as e:
            logger.error(f"URL fetch error: {e}")
            raise InputExtractError(f"Failed to fetch URL: {e}")

    @staticmethod
    async def extract_from_url(url: str, timeout: int = 30) -> tuple[str, str]:
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            raise InputExtractError(f"Invalid URL: {url}")

        try:
            resp = await InputService._safe_request(url, timeout)

            content_type = resp.headers.get("content-type", "").lower()
            filename = Path(parsed.path).name or f"url_{parsed.netloc}"

            if "pdf" in content_type or filename.lower().endswith(".pdf"):
                return InputService._extract_pdf(resp.content), filename
            elif "docx" in content_type or filename.lower().endswith(".docx"):
                return InputService._extract_docx(resp.content), filename
            elif "markdown" in content_type or filename.lower().endswith((".md", ".markdown")):
                return InputService._extract_markdown(resp.content), filename
            elif "html" in content_type or "text" in content_type:
                soup = BeautifulSoup(resp.text, "html.parser")
                for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
                    tag.decompose()
                title = soup.title.get_text(strip=True) if soup.title else filename
                main_content = soup.find("article") or soup.find("main") or soup.find("body") or soup
                text = main_content.get_text(separator="\n")
                text = clean_text(text)
                return text, title
            else:
                return InputService._extract_plain_text(resp.content), filename
        except (httpx.HTTPError, SSRFBlockedError) as e:
            logger.error(f"URL fetch error: {e}")
            raise InputExtractError(f"Failed to fetch URL: {e}")

    @staticmethod
    def extract_from_text(text: str) -> str:
        return clean_text(text)
