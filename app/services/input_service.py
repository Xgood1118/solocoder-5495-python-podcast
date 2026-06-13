import io
import re
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
import pdfplumber
from docx import Document
import markdown

from app.config import get_settings
from app.utils.helpers import clean_text
from app.utils.logging import logger

settings = get_settings()


class InputExtractError(Exception):
    pass


class InputService:
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
    async def extract_from_url(url: str, timeout: int = 30) -> tuple[str, str]:
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            raise InputExtractError(f"Invalid URL: {url}")

        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                resp = await client.get(url, headers={
                    "User-Agent": "Mozilla/5.0 (compatible; PodcastBot/1.0)"
                })
                resp.raise_for_status()

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
        except httpx.HTTPError as e:
            logger.error(f"URL fetch error: {e}")
            raise InputExtractError(f"Failed to fetch URL: {e}")

    @staticmethod
    def extract_from_text(text: str) -> str:
        return clean_text(text)
