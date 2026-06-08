"""小说封面生成服务"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import httpx
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import PROJECT_ROOT
from app.logger import get_logger
from app.models.project import Project
from app.models.settings import Settings
from app.services.cover_providers.base_cover_provider import BaseCoverProvider, CoverGenerationResult
from app.services.cover_providers.gemini_cover_provider import GeminiCoverProvider
from app.services.cover_providers.grok_cover_provider import GrokCoverProvider
from app.services.cover_providers.hermes_cover_provider import HermesCoverProvider
from app.config import settings as app_settings
from app.services.comic_pipeline_utils import should_retry_comic_image_error
from app.services.image_request_utils import (
    build_image_edit_payload,
    append_visible_text_rule,
    decode_b64_image_response,
    normalize_image_api_base_urls,
    normalize_image_bytes_to_png,
    resolve_image_api_base_url,
    resolve_image_edit_model,
    resolve_image_provider_profile,
)
from app.services.prompt_service import PromptService
from app.services.tencent_cos_storage import tencent_cos_storage

logger = get_logger(__name__)

COVER_WIDTH = 1024
COVER_HEIGHT = 1792
GENERATED_COVER_STORAGE_DIR = PROJECT_ROOT / "storage" / "generated_covers"
GENERATED_COVER_PUBLIC_PREFIX = "/generated-assets/covers"


@dataclass
class CoverTestResult:
    success: bool
    message: str
    provider: Optional[str] = None
    model: Optional[str] = None


class CoverGenerationService:
    """封面生成服务"""

    async def generate_cover(
        self,
        *,
        db: AsyncSession,
        user_id: str,
        project_id: str,
        overwrite: bool = True,
    ) -> dict:
        project = await self._get_project(db=db, user_id=user_id, project_id=project_id)
        settings = await self._get_settings(db=db, user_id=user_id)
        self._validate_cover_settings(settings)
        self._validate_cover_storage()

        if project.cover_status == "generating":
            raise HTTPException(status_code=409, detail="封面正在生成中，请勿重复提交")
        if project.cover_status == "ready" and project.cover_image_url and not overwrite:
            raise HTTPException(status_code=400, detail="当前项目已存在封面，如需覆盖请传入 overwrite=true")

        prompt = await PromptService.build_novel_cover_prompt(
            project,
            user_id=user_id,
            db=db,
        )
        prompt = append_visible_text_rule(prompt, settings.image_text_language)
        project.cover_status = "generating"
        project.cover_error = None
        project.cover_prompt = prompt
        await db.commit()
        await db.refresh(project)

        try:
            provider = self._build_provider(settings)
            result = await provider.generate_cover(
                prompt=prompt,
                model=settings.cover_image_model or "",
                width=COVER_WIDTH,
                height=COVER_HEIGHT,
            )
            image_url = await self._save_cover_file(
                user_id=user_id,
                project_id=project.id,
                content=result["content"],
                file_extension=result["file_extension"],
            )

            project.cover_image_url = image_url
            project.cover_status = "ready"
            project.cover_error = None
            project.cover_updated_at = datetime.utcnow()
            project.cover_prompt = prompt
            await db.commit()
            await db.refresh(project)

            return {
                "project_id": project.id,
                "cover_status": project.cover_status,
                "cover_image_url": project.cover_image_url,
                "cover_prompt": project.cover_prompt,
                "provider": result["provider"],
                "model": result["model"],
                "message": "封面生成成功",
            }
        except httpx.HTTPStatusError as exc:
            logger.error("封面生成上游 HTTP 错误: project_id=%s error=%s", project.id, exc, exc_info=True)
            detail = self._extract_upstream_error_detail(exc)
            project.cover_status = "failed"
            project.cover_error = detail
            await db.commit()
            raise HTTPException(status_code=exc.response.status_code, detail=detail) from exc
        except HTTPException as exc:
            logger.error("封面生成业务错误: project_id=%s error=%s", project.id, exc.detail, exc_info=True)
            project.cover_status = "failed"
            project.cover_error = str(exc.detail)
            await db.commit()
            raise
        except Exception as exc:
            logger.error("封面生成失败: project_id=%s error=%s", project.id, exc, exc_info=True)
            project.cover_status = "failed"
            project.cover_error = str(exc)
            await db.commit()
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    async def edit_cover(
        self,
        *,
        db: AsyncSession,
        user_id: str,
        project_id: str,
        prompt: str,
    ) -> dict:
        project = await self._get_project(db=db, user_id=user_id, project_id=project_id)
        settings = await self._get_settings(db=db, user_id=user_id)
        self._validate_cover_settings(settings)
        self._validate_cover_storage()

        edit_instruction = " ".join((prompt or "").split()).strip()
        if not edit_instruction:
            raise HTTPException(status_code=400, detail="请输入封面改图要求")
        if project.cover_status == "generating":
            raise HTTPException(status_code=409, detail="封面正在生成中，请稍后再改图")
        if project.cover_status != "ready" or not project.cover_image_url:
            raise HTTPException(status_code=400, detail="当前项目还没有可改图的封面，请先生成封面")

        previous_cover_status = project.cover_status
        previous_cover_image_url = project.cover_image_url
        previous_cover_prompt = project.cover_prompt
        previous_cover_updated_at = project.cover_updated_at

        original_cover_bytes, _ = await self._read_current_cover_image(project)
        edit_prompt = self._build_cover_edit_prompt(
            project=project,
            edit_instruction=edit_instruction,
            image_text_language=settings.image_text_language,
        )

        project.cover_status = "generating"
        project.cover_error = None
        project.cover_prompt = edit_prompt
        await db.commit()
        await db.refresh(project)

        try:
            result = await self._edit_cover_with_retry(
                edit_prompt,
                original_cover_bytes,
                settings=settings,
            )
            image_url = await self._save_cover_file(
                user_id=user_id,
                project_id=project.id,
                content=result["content"],
                file_extension=result["file_extension"],
            )

            project.cover_image_url = image_url
            project.cover_status = "ready"
            project.cover_error = None
            project.cover_updated_at = datetime.utcnow()
            project.cover_prompt = edit_prompt
            await db.commit()
            await db.refresh(project)

            return {
                "project_id": project.id,
                "cover_status": project.cover_status,
                "cover_image_url": project.cover_image_url,
                "cover_prompt": project.cover_prompt,
                "provider": result["provider"],
                "model": result["model"],
                "message": "封面改图成功",
            }
        except httpx.HTTPStatusError as exc:
            logger.error("封面改图上游 HTTP 错误: project_id=%s error=%s", project.id, exc, exc_info=True)
            detail = self._extract_upstream_error_detail(exc)
            await self._restore_cover_after_edit_failure(
                db=db,
                project=project,
                previous_status=previous_cover_status,
                previous_image_url=previous_cover_image_url,
                previous_prompt=previous_cover_prompt,
                previous_updated_at=previous_cover_updated_at,
                error_detail=detail,
            )
            raise HTTPException(status_code=exc.response.status_code, detail=detail) from exc
        except HTTPException as exc:
            logger.error("封面改图业务错误: project_id=%s error=%s", project.id, exc.detail, exc_info=True)
            await self._restore_cover_after_edit_failure(
                db=db,
                project=project,
                previous_status=previous_cover_status,
                previous_image_url=previous_cover_image_url,
                previous_prompt=previous_cover_prompt,
                previous_updated_at=previous_cover_updated_at,
                error_detail=str(exc.detail),
            )
            raise
        except Exception as exc:
            logger.error("封面改图失败: project_id=%s error=%s", project.id, exc, exc_info=True)
            await self._restore_cover_after_edit_failure(
                db=db,
                project=project,
                previous_status=previous_cover_status,
                previous_image_url=previous_cover_image_url,
                previous_prompt=previous_cover_prompt,
                previous_updated_at=previous_cover_updated_at,
                error_detail=str(exc),
            )
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    async def test_cover_settings(
        self,
        *,
        provider: str,
        api_key: str,
        api_base_url: Optional[str],
        model: str,
    ) -> CoverTestResult:
        if not provider or not api_key or not model:
            raise HTTPException(status_code=400, detail="封面图片配置不完整，请填写 provider、api_key 和 model")

        provider_instance = self._build_provider_from_values(
            provider=provider,
            api_key=api_key,
            api_base_url=api_base_url,
        )
        test_prompt = (
            "Create a clean fantasy novel cover illustration, vertical book cover, "
            "standard 2:3 ratio, atmospheric lighting, no text, no watermark."
        )
        try:
            await provider_instance.generate_cover(
                prompt=test_prompt,
                model=model,
                width=COVER_WIDTH,
                height=COVER_HEIGHT,
            )
        except httpx.HTTPStatusError as exc:
            detail = self._extract_upstream_error_detail(exc)
            raise HTTPException(status_code=exc.response.status_code, detail=detail) from exc

        return CoverTestResult(
            success=True,
            message="封面图片接口测试成功",
            provider=provider,
            model=model,
        )

    async def get_cover_download_target(
        self,
        *,
        db: AsyncSession,
        user_id: str,
        project_id: str,
    ) -> tuple[Project, str | Path]:
        project = await self._get_project(db=db, user_id=user_id, project_id=project_id)
        if project.cover_status != "ready" or not project.cover_image_url:
            raise HTTPException(status_code=404, detail="当前项目尚未生成可下载的封面")

        target = self._resolve_cover_target(project.cover_image_url)
        if isinstance(target, str):
            return project, target
        if not target.exists():
            raise HTTPException(status_code=404, detail="封面文件不存在，请重新生成")
        return project, target

    async def clear_cover_metadata(self, *, db: AsyncSession, project: Project) -> None:
        project.cover_image_url = None
        project.cover_prompt = None
        project.cover_status = "none"
        project.cover_error = None
        project.cover_updated_at = None
        await db.commit()

    async def _get_project(self, *, db: AsyncSession, user_id: str, project_id: str) -> Project:
        result = await db.execute(
            select(Project).where(Project.id == project_id, Project.user_id == user_id)
        )
        project = result.scalar_one_or_none()
        if not project:
            raise HTTPException(status_code=404, detail="项目不存在")
        return project

    async def _get_settings(self, *, db: AsyncSession, user_id: str) -> Settings:
        result = await db.execute(select(Settings).where(Settings.user_id == user_id))
        settings = result.scalar_one_or_none()
        if not settings:
            raise HTTPException(status_code=400, detail="请先在设置页完成封面图片配置")
        return settings

    def _validate_cover_settings(self, settings: Settings) -> None:
        if not settings.cover_enabled:
            raise HTTPException(status_code=400, detail="封面图片功能未启用，请先在设置页开启")
        if not settings.cover_api_provider or not settings.cover_api_key or not settings.cover_image_model:
            raise HTTPException(status_code=400, detail="封面图片配置不完整，请前往设置页补全")

    def _validate_cover_storage(self) -> None:
        if not tencent_cos_storage.is_enabled():
            raise HTTPException(status_code=400, detail="请先配置 Tencent COS，封面图片只保存 COS 地址，不再保存本地文件")

    def _build_provider(self, settings: Settings) -> BaseCoverProvider:
        return self._build_provider_from_values(
            provider=settings.cover_api_provider or "",
            api_key=settings.cover_api_key or "",
            api_base_url=settings.cover_api_base_url,
        )

    def _build_provider_from_values(
        self,
        *,
        provider: str,
        api_key: str,
        api_base_url: Optional[str],
    ) -> BaseCoverProvider:
        provider_value = (provider or "").lower().strip()
        normalized_base_url = (api_base_url or "").rstrip("/")
        if provider_value in {"hermes", "openai"}:
            default_base_url = app_settings.HERMES_IMAGE_BASE_URL if provider_value == "hermes" else ""
            return HermesCoverProvider(
                api_key=api_key or app_settings.HERMES_IMAGE_API_KEY or "",
                base_url=resolve_image_api_base_url(
                    provider=provider_value,
                    base_url=normalized_base_url or default_base_url or "",
                ),
                default_model=app_settings.HERMES_IMAGE_MODEL,
            )
        if provider_value == "gemini":
            return GeminiCoverProvider(api_key=api_key, base_url=normalized_base_url)
        if provider_value == "grok":
            return GrokCoverProvider(api_key=api_key, base_url=normalized_base_url)
        if provider_value == "mumu":
            if not normalized_base_url:
                raise HTTPException(status_code=400, detail="请先配置朱雀API图片服务地址")
            if normalized_base_url.endswith("/v1beta"):
                return GeminiCoverProvider(api_key=api_key, base_url=normalized_base_url)
            return GrokCoverProvider(api_key=api_key, base_url=normalized_base_url)
        raise HTTPException(status_code=400, detail="当前版本支持 OpenAI、Hermes、Gemini、Grok 或 朱雀API 作为封面图片 Provider")

    async def _save_cover_file(
        self,
        *,
        user_id: str,
        project_id: str,
        content: bytes,
        file_extension: str,
    ) -> str:
        timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
        safe_extension = (file_extension or "png").lstrip(".")
        filename = f"{project_id}_{timestamp}.{safe_extension}"
        object_key = tencent_cos_storage.build_object_key("covers", user_id, filename)
        metadata = await tencent_cos_storage.upload_bytes(
            object_key=object_key,
            content=content,
            content_type=tencent_cos_storage.guess_content_type(filename, default="image/png"),
        )
        logger.info("封面文件已上传 COS: project_id=%s key=%s", project_id, metadata.object_key)
        return metadata.url

    async def _read_current_cover_image(self, project: Project) -> tuple[bytes, str | None]:
        target = self._resolve_cover_target(project.cover_image_url)
        if isinstance(target, Path):
            if not target.exists():
                raise HTTPException(status_code=404, detail="封面文件不存在，请重新生成")
            return target.read_bytes(), tencent_cos_storage.guess_content_type(target.name, default="image/png")

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=15.0), follow_redirects=True) as client:
                response = await client.get(target)
                response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise HTTPException(status_code=503, detail="读取当前封面图片超时，请稍后重试") from exc
        except httpx.HTTPStatusError as exc:
            raise HTTPException(status_code=502, detail=f"读取当前封面图片失败: HTTP {exc.response.status_code}") from exc
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"读取当前封面图片失败: {exc}") from exc
        if not response.content:
            raise HTTPException(status_code=404, detail="当前封面图片内容为空，请重新生成")
        return response.content, response.headers.get("content-type")

    def _build_cover_edit_prompt(
        self,
        *,
        project: Project,
        edit_instruction: str,
        image_text_language: str | None = None,
    ) -> str:
        base_prompt = (project.cover_prompt or "").strip()
        project_title = (project.title or "未命名小说").strip()
        prompt_parts = [
            f"基于当前小说《{project_title}》封面进行改图。",
            "保留原封面的主体构图、书名识别度、出版级封面质感和竖版书籍封面比例。",
            "只根据用户修改要求调整画面，不要改成无关题材，不要添加水印、logo、UI 元素或样机展示。",
            f"用户修改要求：{edit_instruction}",
        ]
        if base_prompt:
            prompt_parts.append(f"原封面生成提示词参考：{base_prompt}")
        return append_visible_text_rule("\n".join(prompt_parts), image_text_language)

    async def _edit_cover_with_retry(
        self,
        prompt: str,
        image_bytes: bytes,
        *,
        settings: Settings,
    ) -> CoverGenerationResult:
        max_attempts = 4
        last_exc: Exception | None = None
        for attempt in range(1, max_attempts + 1):
            try:
                return await self._call_cover_edit_api(
                    prompt,
                    image_bytes,
                    settings=settings,
                )
            except Exception as exc:
                last_exc = exc
                if attempt < max_attempts and self._should_retry_cover_edit_error(exc):
                    wait_seconds = self._cover_edit_retry_delay(attempt, exc)
                    logger.warning(
                        "封面改图遇到上游波动，准备重试: attempt=%s/%s wait=%ss error=%s",
                        attempt,
                        max_attempts,
                        wait_seconds,
                        str(exc)[:200],
                    )
                    await asyncio.sleep(wait_seconds)
                    continue
                raise
        raise last_exc or HTTPException(status_code=502, detail="封面改图失败")

    async def _call_cover_edit_api(
        self,
        prompt: str,
        image_bytes: bytes,
        *,
        settings: Settings,
    ) -> CoverGenerationResult:
        configured_base_url = resolve_image_api_base_url(
            provider=settings.cover_api_provider,
            base_url=settings.cover_api_base_url,
            model=settings.cover_image_model,
        )
        api_key = settings.cover_api_key or ""
        model = settings.cover_image_model or ""
        if not configured_base_url or not api_key or not model:
            raise HTTPException(status_code=400, detail="图片接口未配置，请在设置页配置封面图片的 API Key、API 地址和模型")

        normalized_image_bytes, source_format, image_issue = normalize_image_bytes_to_png(image_bytes)
        if image_issue:
            raise HTTPException(status_code=400, detail=f"当前封面图片无法用于改图: {image_issue}")
        if source_format and source_format != "png":
            logger.info("封面改图输入图片已归一化为 PNG: source_format=%s bytes=%s", source_format, len(normalized_image_bytes))

        provider_profile = resolve_image_provider_profile(
            provider=settings.cover_api_provider,
            base_url=configured_base_url,
            model=model,
        )
        edit_model = resolve_image_edit_model(model, provider_profile=provider_profile)
        headers = {"Authorization": f"Bearer {api_key}"}
        base_candidates = normalize_image_api_base_urls(configured_base_url) or [configured_base_url]
        logger.info("封面改图使用图片接口候选路径: %s", base_candidates)

        response: httpx.Response | None = None
        async with httpx.AsyncClient(timeout=httpx.Timeout(600.0, connect=15.0)) as client:
            for candidate_url in base_candidates:
                try:
                    response = await client.post(
                        f"{candidate_url}/images/edits",
                        headers=headers,
                        data=build_image_edit_payload(
                            prompt,
                            model=edit_model,
                            size=f"{COVER_WIDTH}x{COVER_HEIGHT}",
                            provider_profile=provider_profile,
                        ),
                        files={"image": ("cover.png", normalized_image_bytes, "image/png")},
                    )
                    response.raise_for_status()
                    break
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code == 404 and candidate_url != base_candidates[-1]:
                        logger.warning("封面改图接口 404，尝试下一个候选路径: %s", candidate_url)
                        continue
                    raise
                except httpx.TimeoutException as exc:
                    raise HTTPException(status_code=503, detail="封面改图接口响应超时，请稍后重试") from exc
                except httpx.HTTPError as exc:
                    raise HTTPException(status_code=502, detail=f"封面改图请求失败: {exc}") from exc

        if response is None:
            raise HTTPException(status_code=502, detail="封面改图接口候选路径全部不可用")

        try:
            response_data = response.json()
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=502, detail="封面改图接口返回了无效 JSON") from exc

        try:
            edited_bytes, revised_prompt = decode_b64_image_response(response_data)
        except ValueError as exc:
            raise HTTPException(status_code=502, detail=f"封面改图接口错误: {exc}") from exc

        return CoverGenerationResult(
            content=edited_bytes,
            mime_type="image/png",
            file_extension="png",
            revised_prompt=revised_prompt,
            provider=(settings.cover_api_provider or "").strip() or "cover-api",
            model=edit_model,
        )

    async def _restore_cover_after_edit_failure(
        self,
        *,
        db: AsyncSession,
        project: Project,
        previous_status: str,
        previous_image_url: str | None,
        previous_prompt: str | None,
        previous_updated_at: datetime | None,
        error_detail: str,
    ) -> None:
        project.cover_status = previous_status if previous_image_url else "failed"
        project.cover_image_url = previous_image_url
        project.cover_prompt = previous_prompt
        project.cover_updated_at = previous_updated_at
        project.cover_error = error_detail
        await db.commit()

    def _should_retry_cover_edit_error(self, exc: Exception) -> bool:
        if isinstance(exc, httpx.HTTPStatusError):
            return should_retry_comic_image_error(exc.response.status_code, self._extract_upstream_error_detail(exc))
        if isinstance(exc, HTTPException):
            return should_retry_comic_image_error(exc.status_code, str(exc.detail))
        return should_retry_comic_image_error(None, str(exc))

    def _cover_edit_retry_delay(self, attempt: int, exc: Exception) -> int:
        detail = self._cover_edit_error_detail_for_retry(exc).lower()
        if "429" in detail or "rate limit" in detail or "too many requests" in detail or "appchatreverse" in detail:
            return min(10 * attempt, 45)
        return min(3 * attempt, 15)

    def _cover_edit_error_detail_for_retry(self, exc: Exception) -> str:
        if isinstance(exc, httpx.HTTPStatusError):
            return self._extract_upstream_error_detail(exc)
        if isinstance(exc, HTTPException):
            return str(exc.detail)
        return str(exc)

    def _resolve_cover_target(self, cover_image_url: Optional[str]) -> str | Path:
        if not cover_image_url:
            raise HTTPException(status_code=404, detail="当前项目尚未生成可下载的封面")

        parsed = urlparse(cover_image_url)
        if parsed.scheme in {"http", "https"} and parsed.netloc:
            return cover_image_url

        if cover_image_url.startswith(f"{GENERATED_COVER_PUBLIC_PREFIX}/"):
            relative_path = cover_image_url.replace(f"{GENERATED_COVER_PUBLIC_PREFIX}/", "", 1)
            return GENERATED_COVER_STORAGE_DIR / relative_path

        if cover_image_url.startswith("/assets/generated_covers/"):
            relative_path = cover_image_url.replace("/assets/generated_covers/", "", 1)
            return GENERATED_COVER_STORAGE_DIR / relative_path

        raise HTTPException(status_code=404, detail="封面文件路径无效，请重新生成")

    @staticmethod
    def _extract_upstream_error_detail(exc: httpx.HTTPStatusError) -> str:
        response = exc.response
        if response is None:
            return str(exc)

        try:
            data = response.json()
        except json.JSONDecodeError:
            text = response.text.strip()
            return text or str(exc)

        if isinstance(data, dict):
            for key in ("detail", "message", "error", "msg"):
                value = data.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
                if isinstance(value, dict):
                    for nested_key in ("message", "detail", "msg"):
                        nested_value = value.get(nested_key)
                        if isinstance(nested_value, str) and nested_value.strip():
                            return nested_value.strip()
                if isinstance(value, list) and value:
                    first_item = value[0]
                    if isinstance(first_item, str) and first_item.strip():
                        return first_item.strip()

        text = response.text.strip()
        if text:
            return text
        return str(exc)


cover_generation_service = CoverGenerationService()
