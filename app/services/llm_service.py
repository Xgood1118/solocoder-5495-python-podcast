import json
import re
from typing import List, Optional

from openai import OpenAI

from app.config import get_settings
from app.schemas import PodcastScript, ScriptSegment
from app.utils.helpers import chunk_text, async_retry
from app.utils.logging import logger

settings = get_settings()

SCRIPT_PROMPT_TEMPLATE = """你是一位专业的播客编剧，擅长将技术/商业内容转化为生动有趣的双人对话。

## 任务
将以下原始文本改写成一个双人对话播客脚本：主持人A（好奇提问型）和主持人B（专业讲解型）。

## 要求
1. 对话自然流畅，口语化，避免生硬念稿
2. 主持人A负责提问、引导、追问、总结，语气可以好奇、兴奋、疑惑
3. 主持人B负责讲解、举例、类比、深入分析，语气可以专业、严肃、热情
4. 每段对话不宜过长（单段不超过150字），适合语音合成
5. 情绪标签可选: neutral(中性) / curious(好奇) / excited(兴奋) / serious(严肃) / happy(开心) / thoughtful(思考)
6. 保留所有核心信息点，不遗漏关键内容
7. 开头有欢迎引入，结尾有总结收尾
8. 使用与原文相同的语言（中文或英文等）

## 原始内容
标题：{title}
语言：{language}
正文：
{content}

## 输出格式
请严格输出JSON格式，不要包含任何其他文字：
```json
{{
  "title": "播客标题",
  "summary": "一句话内容摘要",
  "language": "{language}",
  "segments": [
    {{"speaker": "A", "text": "对话内容", "emotion": "curious"}},
    {{"speaker": "B", "text": "对话内容", "emotion": "serious"}}
  ]
}}
```
"""


class LLMService:
    def __init__(self):
        self.client = OpenAI(
            api_key=settings.LLM_API_KEY,
            base_url=settings.LLM_API_BASE,
            timeout=settings.LLM_TIMEOUT,
        )
        self.model = settings.LLM_MODEL

    def _parse_json_output(self, raw: str) -> dict:
        raw = raw.strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)
        match = re.search(r"\{[\s\S]*\}", raw)
        if match:
            raw = match.group(0)
        try:
            return json.loads(raw)
        except json.JSONDecodeError as e:
            logger.warning(f"JSON parse failed, trying to fix: {e}")
            fixed = raw.replace("'", '"')
            try:
                return json.loads(fixed)
            except json.JSONDecodeError:
                raise ValueError(f"Failed to parse LLM output as JSON: {raw[:500]}")

    @async_retry(max_retries=2, delay=3.0)
    async def generate_script(
        self,
        raw_text: str,
        title: str = "Untitled",
        language: str = "zh",
        max_chars_per_chunk: int = 6000,
    ) -> PodcastScript:
        if len(raw_text) <= max_chars_per_chunk:
            return await self._generate_single_script(raw_text, title, language)

        chunks = chunk_text(raw_text, max_chars_per_chunk)
        logger.info(f"Text split into {len(chunks)} chunks for script generation")
        all_segments: List[ScriptSegment] = []
        combined_summary = ""

        for i, chunk in enumerate(chunks):
            chunk_title = f"{title} (第{i+1}/{len(chunks)}部分)"
            script = await self._generate_single_script(chunk, chunk_title, language)
            if i == 0:
                combined_summary = script.summary
            all_segments.extend(script.segments)

        return PodcastScript(
            title=title,
            summary=combined_summary,
            language=language,
            segments=all_segments,
        )

    async def _generate_single_script(
        self, content: str, title: str, language: str
    ) -> PodcastScript:
        prompt = SCRIPT_PROMPT_TEMPLATE.format(
            title=title,
            language=language,
            content=content,
        )

        logger.info(f"Calling LLM for script generation, content length: {len(content)}")

        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": "你是专业的播客内容创作者，擅长将文档转化为生动的对话。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.7,
            response_format={"type": "json_object"},
        )

        raw_output = response.choices[0].message.content or ""
        data = self._parse_json_output(raw_output)

        segments = []
        for seg in data.get("segments", []):
            speaker = seg.get("speaker", "A").upper()
            if speaker not in ("A", "B"):
                speaker = "B"
            text = seg.get("text", "").strip()
            if not text:
                continue
            emotion = seg.get("emotion", "neutral") or "neutral"
            segments.append(ScriptSegment(speaker=speaker, text=text, emotion=emotion))

        if not segments:
            raise ValueError("LLM returned empty segments