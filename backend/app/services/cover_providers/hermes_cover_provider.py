"""Hermes 统一图片生成 Provider — 封面专用"""
from __future__ import annotations

import json
from typing import Optional

import httpx

from app.logger import get_logger
from app.services.cover_providers.base_cover_provider import BaseCoverProvider, CoverGenerationResult
from app.services.image_request_utils import build_image_generation_payload, decode_b64_image_response, resolve_image_provider_profile

logger = get_logger(__name__)


class HermesCoverProvider(BaseCoverProvider):
    """基于 Hermes 图片接口的封面生成实现（通用 OpenAI-compatible images/generations）"""

    def __init__(self, api_key: str, base_url: str, default_model: str = "grok-imagine-1.0"):
        self.api_key = api_key
        self.base_url = (base_url or "").rstrip("/")
        self.default_model = default_model or "grok-imagine-1.0"

    async def generate_cover(
        self,
        *,
        prompt: str,
        model: str,
        width: int,
        height: int,
    ) -> CoverGenerationResult:
        effective_model = model or self.default_model
        url = f"{self.base_url}/images/generations"
        size = f"{width}x{height}"
        provider_profile = resolve_image_provider_profile(
            provider="openai" if "api.openai.com" in self.base_url else "hermes",
            base_url=self.base_url,
            model=effective_model,
        )
        payload = build_image_generation_payload(
            prompt,
            model=effective_model,
            size=size,
            provider_profile=provider_profile,
        )
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        logger.info(
            "Hermes 封面生成请求: url=%s model=%s size=%s prompt_len=%s",
            url, effective_model, size, len(prompt or ""),
        )

        try:
            async with httpx.AsyncClient(timeout=600.0) as client:
                response = await client.post(url, headers=headers, json=payload)

            logger.debug(
                "Hermes 封面生成响应: status=%s body_preview=%s",
                response.status_code,
                response.text[:500],
            )

            response.raise_for_status()
            data = response.json()
        except json.JSONDecodeError as exc:
            logger.error("Hermes 封面生成返回了无效 JSON", exc_info=True)
            raise ValueError("Hermes 返回了无效 JSON") from exc
        except httpx.HTTPStatusError as exc:
            logger.error(
                "Hermes 封面生成 HTTP 错误: status=%s response=%s",
                exc.response.status_code if exc.response else None,
                exc.response.text[:2000] if exc.response is not None else None,
            )
            raise
        except Exception:
            logger.error("Hermes 封面生成请求异常", exc_info=True)
            raise

        decoded_content, revised_prompt = decode_b64_image_response(data)
        logger.info("Hermes 封面生成成功: bytes=%s revised_prompt=%s", len(decoded_content), bool(revised_prompt))

        return CoverGenerationResult(
            content=decoded_content,
            mime_type="image/png",
            file_extension="png",
            revised_prompt=revised_prompt if isinstance(revised_prompt, str) else None,
            provider="hermes",
            model=effective_model,
        )
