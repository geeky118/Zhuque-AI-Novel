"""角色形象图 API。"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Optional
import uuid

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.common import verify_project_access
from app.config import settings
from app.database import get_db, get_engine
from app.services.agents import CharacterImageWorkflowAgent
from app.models.settings import Settings
from app.logger import get_logger
from app.models.character import Character
from app.models.media_artifact import CharacterImageArtifact
from app.models.project import Project
from app.services.comic_style import build_comic_style_instruction
from app.services.character_image_prompt import (
    FEMALE_IMAGE_SUFFIX,
    MALE_IMAGE_SUFFIX,
    ORGANIZATION_IMAGE_SUFFIX,
    build_character_image_prompt,
    normalize_text as _normalize_text,
    pick_image_suffix,
    sanitize_prompt_text as _sanitize_prompt_text,
)
from app.services.image_request_utils import (
    build_image_edit_payload,
    build_image_generation_payload,
    decode_b64_image_response,
    append_visible_text_rule,
    normalize_image_api_base_urls,
    normalize_image_text_language,
    resolve_image_api_base_url,
    resolve_image_edit_model,
    resolve_image_provider_profile,
)
from app.services.tencent_cos_storage import COSObjectMetadata, tencent_cos_storage

router = APIRouter(prefix="/character-images", tags=["角色形象图"])
logger = get_logger(__name__)

CHARACTER_IMAGE_ROOT = Path(__file__).parent.parent.parent / "storage" / "character_images"
IMAGE_SIZE = "1024x1024"
CAPACITY_HINTS = (
    "upstream",
    "capacity",
    "bad gateway",
    "service unavailable",
    "gateway timeout",
    "temporarily unavailable",
    "overloaded",
    "timeout",
)
POLICY_HINTS = (
    "policy",
    "safety",
    "community",
    "moderation",
    "content policy",
)
DEFAULT_VARIANT_KEY = "default"
DEFAULT_VARIANT_LABEL = "默认形象"
VARIANT_TYPE_LABELS = {
    "default": "默认",
    "volume": "分卷",
    "period": "时期",
}

CHARACTER_IMAGE_ROOT.mkdir(parents=True, exist_ok=True)
BIBLE_MANIFEST_KEY = "generated_images"

_state_locks: dict[str, asyncio.Lock] = {}
_state_locks_guard = asyncio.Lock()


def _require_cos_image_storage() -> None:
    if not tencent_cos_storage.is_enabled():
        raise HTTPException(status_code=400, detail="请先配置 Tencent COS，生成图片只保存 COS 地址，不再保存本地文件")


class CharacterImageStateResponse(BaseModel):
    character_id: str
    project_id: str
    name: str
    variant_key: str = DEFAULT_VARIANT_KEY
    variant_label: str = DEFAULT_VARIANT_LABEL
    variant_type: str = "default"
    chapter_start: Optional[int] = None
    chapter_end: Optional[int] = None
    sort_order: int = 0
    variant_count: int = 1
    prompt: str
    image_url: Optional[str] = None
    status: str
    updated_at: Optional[str] = None
    error: Optional[str] = None
    error_type: Optional[str] = None
    file_name: Optional[str] = None
    has_image: bool = False
    consistency_key: Optional[str] = None


class CharacterImageListResponse(BaseModel):
    project_id: str
    total: int
    items: list[CharacterImageStateResponse]


class CharacterImageVariantListResponse(BaseModel):
    character_id: str
    project_id: str
    name: str
    total: int
    items: list[CharacterImageStateResponse]


class CharacterImagePromptUpdateRequest(BaseModel):
    prompt: str = Field(..., min_length=10, max_length=2000)


class CharacterImageGenerateRequest(BaseModel):
    overwrite: bool = True


class CharacterImageEditRequest(BaseModel):
    prompt: str = Field(..., min_length=10, max_length=2000)


class CharacterImageVariantCreateRequest(BaseModel):
    variant_label: str = Field(..., min_length=1, max_length=80)
    variant_type: str = Field(..., pattern="^(volume|period)$")
    chapter_start: Optional[int] = Field(default=None, ge=1)
    chapter_end: Optional[int] = Field(default=None, ge=1)
    prompt: Optional[str] = Field(default=None, min_length=10, max_length=2000)


class CharacterImageVariantUpdateRequest(BaseModel):
    variant_label: Optional[str] = Field(default=None, min_length=1, max_length=80)
    variant_type: Optional[str] = Field(default=None, pattern="^(default|volume|period)$")
    chapter_start: Optional[int] = Field(default=None, ge=1)
    chapter_end: Optional[int] = Field(default=None, ge=1)
    prompt: Optional[str] = Field(default=None, min_length=10, max_length=2000)


class CharacterImageInitializeRequest(BaseModel):
    overwrite: bool = False
    limit: Optional[int] = Field(default=None, ge=1, le=50)


class CharacterImageActionResponse(CharacterImageStateResponse):
    message: str
    task_id: Optional[str] = None
    queued: bool = False


class CharacterImageInitializeResponse(BaseModel):
    project_id: str
    total_candidates: int
    character_candidates: int = 0
    organization_candidates: int = 0
    generated: int
    skipped: int
    failed: int
    character_processed: int = 0
    organization_processed: int = 0
    items: list[CharacterImageActionResponse]


class ImageGenerationError(Exception):
    def __init__(self, error_type: str, detail: str, status_code: int = 500):
        super().__init__(detail)
        self.error_type = error_type
        self.detail = detail
        self.status_code = status_code


def _utc_now() -> datetime:
    return datetime.utcnow().replace(microsecond=0)


def _utc_now_iso() -> str:
    return _utc_now().isoformat() + "Z"


def _datetime_to_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.isoformat() + "Z"
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


async def _get_state_lock(project_id: str) -> asyncio.Lock:
    async with _state_locks_guard:
        if project_id not in _state_locks:
            _state_locks[project_id] = asyncio.Lock()
        return _state_locks[project_id]


def _project_state_dir(project_id: str) -> Path:
    path = CHARACTER_IMAGE_ROOT / project_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def _project_state_file(project_id: str) -> Path:
    return _project_state_dir(project_id) / "state.json"


def _variant_image_file(project_id: str, character_id: str, variant_key: str) -> Path:
    if variant_key == DEFAULT_VARIANT_KEY:
        return _project_state_dir(project_id) / f"{character_id}.png"
    return _project_state_dir(project_id) / f"{character_id}__{variant_key}.png"


def _safe_filename_part(value: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in ("-", "_") else "_" for char in value)
    return cleaned.strip("_") or "default"


def _new_variant_image_file(project_id: str, character_id: str, variant_key: str, image_revision: int) -> Path:
    safe_variant = _safe_filename_part(variant_key)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
    suffix = uuid.uuid4().hex[:8]
    if variant_key == DEFAULT_VARIANT_KEY:
        file_name = f"{character_id}__v{image_revision}__{timestamp}_{suffix}.png"
    else:
        file_name = f"{character_id}__{safe_variant}__v{image_revision}__{timestamp}_{suffix}.png"
    return _project_state_dir(project_id) / file_name


def _normalize_variant_type(value: Optional[str]) -> str:
    return value if value in VARIANT_TYPE_LABELS else "default"


def _normalize_variant_label(value: Optional[str], variant_type: str) -> str:
    cleaned = _normalize_text(value)
    if cleaned:
        return cleaned
    return DEFAULT_VARIANT_LABEL if variant_type == "default" else f"{VARIANT_TYPE_LABELS.get(variant_type, '形象')}版本"


def _validate_chapter_scope(chapter_start: Optional[int], chapter_end: Optional[int]) -> None:
    if chapter_start is not None and chapter_end is not None and chapter_start > chapter_end:
        raise HTTPException(status_code=400, detail="章节范围无效，起始章节不能大于结束章节")


def _variant_sort_key(entry: dict[str, Any]) -> tuple[int, int, int, str]:
    is_default = 0 if entry.get("variant_key") == DEFAULT_VARIANT_KEY else 1
    chapter_start = int(entry.get("chapter_start") or 10**9)
    sort_order = int(entry.get("sort_order") or 0)
    variant_label = str(entry.get("variant_label") or "")
    return (is_default, sort_order, chapter_start, variant_label)


def _new_variant_key(variant_type: str) -> str:
    return f"{variant_type}-{uuid.uuid4().hex[:8]}"


def _strip_has_image(entry: dict[str, Any]) -> dict[str, Any]:
    cleaned = dict(entry)
    cleaned.pop("has_image", None)
    return cleaned


def _default_variant_state_entry(project: Project, character: Character, fallback: dict | None = None) -> dict[str, Any]:
    fallback = fallback or {}
    prompt = fallback.get("prompt") or _build_default_prompt(project, character)
    entry = {
        "variant_key": DEFAULT_VARIANT_KEY,
        "variant_label": DEFAULT_VARIANT_LABEL,
        "variant_type": "default",
        "chapter_start": None,
        "chapter_end": None,
        "sort_order": 0,
        "prompt": prompt,
        "status": fallback.get("status") or "none",
        "updated_at": fallback.get("updated_at"),
        "error": fallback.get("error"),
        "error_type": fallback.get("error_type"),
        "file_name": fallback.get("file_name"),
        "local_path": None,
        "cos_bucket": fallback.get("cos_bucket"),
        "cos_region": fallback.get("cos_region"),
        "cos_object_key": fallback.get("cos_object_key"),
        "cos_url": fallback.get("cos_url"),
        "content_type": fallback.get("content_type"),
        "content_length": fallback.get("content_length"),
        "image_revision": _normalize_image_revision(fallback.get("image_revision")),
        "consistency_key": fallback.get("consistency_key"),
    }
    entry["has_image"] = _entry_has_image(entry)
    return entry


def _normalize_variant_state_entry(project: Project, character: Character, variant_key: str, raw_entry: dict | None = None) -> dict[str, Any]:
    raw_entry = raw_entry or {}
    variant_type = _normalize_variant_type(raw_entry.get("variant_type"))
    variant_label = _normalize_variant_label(raw_entry.get("variant_label"), variant_type)
    chapter_start = raw_entry.get("chapter_start")
    chapter_end = raw_entry.get("chapter_end")
    if not raw_entry.get("prompt"):
        prompt = _build_default_prompt(project, character)
    else:
        prompt = str(raw_entry.get("prompt"))
    entry = {
        "variant_key": variant_key,
        "variant_label": variant_label,
        "variant_type": variant_type,
        "chapter_start": chapter_start,
        "chapter_end": chapter_end,
        "sort_order": raw_entry.get("sort_order") or 0,
        "prompt": prompt,
        "status": raw_entry.get("status") or "none",
        "updated_at": raw_entry.get("updated_at"),
        "error": raw_entry.get("error"),
        "error_type": raw_entry.get("error_type"),
        "file_name": raw_entry.get("file_name"),
        "local_path": None,
        "cos_bucket": raw_entry.get("cos_bucket"),
        "cos_region": raw_entry.get("cos_region"),
        "cos_object_key": raw_entry.get("cos_object_key"),
        "cos_url": raw_entry.get("cos_url"),
        "content_type": raw_entry.get("content_type"),
        "content_length": raw_entry.get("content_length"),
        "image_revision": _normalize_image_revision(raw_entry.get("image_revision")),
        "consistency_key": raw_entry.get("consistency_key"),
    }
    entry["has_image"] = _entry_has_image(entry)
    return entry


def _read_state_from_disk(project_id: str) -> dict[str, dict[str, dict[str, Any]]]:
    state_file = _project_state_file(project_id)
    if not state_file.exists():
        return {}

    try:
        raw = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("角色形象状态文件读取失败，已重置: project_id=%s", project_id, exc_info=True)
        return {}

    if isinstance(raw, dict) and isinstance(raw.get("characters"), dict):
        raw_characters = raw["characters"]
    elif isinstance(raw, dict):
        raw_characters = raw
    else:
        raw_characters = {}

    normalized: dict[str, dict[str, dict[str, Any]]] = {}
    for character_id, character_state in raw_characters.items():
        if not isinstance(character_state, dict):
            continue
        raw_variants = character_state.get("variants") if isinstance(character_state.get("variants"), dict) else None
        if raw_variants is None:
            raw_variants = {DEFAULT_VARIANT_KEY: character_state}
        normalized[character_id] = {
            str(variant_key): dict(entry)
            for variant_key, entry in raw_variants.items()
            if isinstance(entry, dict)
        }
    return normalized


def _write_state_to_disk(project_id: str, state: dict[str, dict[str, dict[str, Any]]]) -> None:
    state_file = _project_state_file(project_id)
    payload = {
        "characters": {
            character_id: {
                "variants": {
                    variant_key: _strip_has_image(entry)
                    for variant_key, entry in sorted(variants.items(), key=lambda item: _variant_sort_key(item[1]))
                }
            }
            for character_id, variants in state.items()
        }
    }
    state_file.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _ensure_prompt_suffix(prompt: str, gender: Optional[str] = None, is_organization: bool = False) -> str:
    base = _sanitize_prompt_text(prompt)
    suffix = ORGANIZATION_IMAGE_SUFFIX if is_organization else pick_image_suffix(gender)
    known_suffixes = (MALE_IMAGE_SUFFIX, FEMALE_IMAGE_SUFFIX, ORGANIZATION_IMAGE_SUFFIX)
    if not any(known_suffix in base for known_suffix in known_suffixes):
        base = f"{base} {suffix}".strip()
    return base[:1600]


def _project_comic_style_instruction(project: Project) -> str:
    return build_comic_style_instruction(
        getattr(project, "comic_style", None),
        getattr(project, "comic_style_prompt", None),
    )


def _append_project_comic_style(prompt: str, project: Project) -> str:
    instruction = _project_comic_style_instruction(project)
    if instruction in prompt:
        return prompt
    return f"{prompt.strip()}\n\n{instruction}".strip()


def _build_default_prompt(project: Project, character: Character) -> str:
    return build_character_image_prompt(
        title=project.title,
        name=character.name,
        gender=character.gender,
        age=character.age,
        appearance=character.appearance,
        is_organization=character.is_organization,
        organization_type=character.organization_type,
        organization_purpose=character.organization_purpose,
        comic_style_instruction=_project_comic_style_instruction(project),
    )


def _normalize_image_revision(value: Any) -> int:
    try:
        return max(int(value or 0), 0)
    except (TypeError, ValueError):
        return 0


def _next_image_revision(entry: dict[str, Any]) -> int:
    return _normalize_image_revision(entry.get("image_revision")) + 1


def _image_no_cache_headers() -> dict[str, str]:
    return {
        "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
        "Pragma": "no-cache",
        "Expires": "0",
    }


def _build_image_url(
    entry: dict,
) -> Optional[str]:
    if not entry.get("has_image"):
        return None
    cos_url = entry.get("cos_url")
    if isinstance(cos_url, str) and cos_url.strip():
        return cos_url
    cos_object_key = entry.get("cos_object_key")
    if isinstance(cos_object_key, str) and cos_object_key.strip() and tencent_cos_storage.is_enabled():
        return tencent_cos_storage.public_url(cos_object_key)
    return None


def _entry_has_image(entry: dict) -> bool:
    return bool(entry.get("cos_url") or entry.get("cos_object_key"))


def _artifact_to_entry(artifact: CharacterImageArtifact | None, fallback: dict, character: Character) -> dict:
    updated_at = _datetime_to_iso(artifact.updated_at) if artifact else fallback.get("updated_at")
    entry = {
        "variant_key": DEFAULT_VARIANT_KEY,
        "variant_label": DEFAULT_VARIANT_LABEL,
        "variant_type": "default",
        "chapter_start": None,
        "chapter_end": None,
        "sort_order": 0,
        "prompt": (artifact.prompt if artifact else None) or fallback.get("prompt") or "",
        "status": (artifact.status if artifact else None) or fallback.get("status") or "none",
        "updated_at": updated_at,
        "error": artifact.error if artifact else fallback.get("error"),
        "error_type": artifact.error_type if artifact else fallback.get("error_type"),
        "file_name": (artifact.file_name if artifact else None) or fallback.get("file_name"),
        "local_path": None,
        "cos_bucket": artifact.cos_bucket if artifact else fallback.get("cos_bucket"),
        "cos_region": artifact.cos_region if artifact else fallback.get("cos_region"),
        "cos_object_key": artifact.cos_object_key if artifact else fallback.get("cos_object_key"),
        "cos_url": artifact.cos_url if artifact else fallback.get("cos_url"),
        "content_type": artifact.content_type if artifact else fallback.get("content_type"),
        "content_length": artifact.content_length if artifact else fallback.get("content_length"),
        "image_revision": _normalize_image_revision(fallback.get("image_revision")),
    }
    entry["has_image"] = _entry_has_image(entry)
    return entry


def _build_state_response(character: Character, entry: dict, variant_count: int = 1) -> CharacterImageStateResponse:
    has_image = bool(entry.get("has_image"))
    return CharacterImageStateResponse(
        character_id=character.id,
        project_id=character.project_id,
        name=character.name,
        variant_key=entry.get("variant_key") or DEFAULT_VARIANT_KEY,
        variant_label=entry.get("variant_label") or DEFAULT_VARIANT_LABEL,
        variant_type=entry.get("variant_type") or "default",
        chapter_start=entry.get("chapter_start"),
        chapter_end=entry.get("chapter_end"),
        sort_order=int(entry.get("sort_order") or 0),
        variant_count=variant_count,
        prompt=entry.get("prompt") or "",
        image_url=_build_image_url(entry),
        status=entry.get("status") or "none",
        updated_at=entry.get("updated_at"),
        error=entry.get("error"),
        error_type=entry.get("error_type"),
        file_name=entry.get("file_name"),
        has_image=has_image,
        consistency_key=entry.get("consistency_key"),
    )


def _extract_error_detail(exc: httpx.HTTPStatusError) -> str:
    response = exc.response
    if response is None:
        return str(exc)

    try:
        data = response.json()
    except json.JSONDecodeError:
        text = response.text.strip()
        return text or str(exc)

    if isinstance(data, dict):
        error_value = data.get("error")
        if isinstance(error_value, dict):
            for key in ("message", "detail", "msg"):
                nested_value = error_value.get(key)
                if isinstance(nested_value, str) and nested_value.strip():
                    return nested_value.strip()
            code = error_value.get("code")
            if isinstance(code, str) and code.strip():
                return code.strip()
        for key in ("detail", "message", "msg", "error"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

    text = response.text.strip()
    return text or str(exc)


def _classify_generation_error(status_code: int, detail: str) -> tuple[str, int]:
    lowered = (detail or "").lower()
    if status_code in (429, 502, 503, 504) or any(hint in lowered for hint in CAPACITY_HINTS):
        return "capacity", 503
    if any(hint in lowered for hint in POLICY_HINTS):
        return "policy", 422
    return "failed", status_code if status_code >= 400 else 500


async def _load_image_settings(user_id: str, db: AsyncSession) -> dict[str, Any]:
    """从用户 Settings 表读取图片生成配置。"""
    result = await db.execute(select(Settings).where(Settings.user_id == user_id))
    user_settings = result.scalar_one_or_none()
    if not user_settings:
        raise HTTPException(status_code=400, detail="请先在设置页完成图片生成配置")
    api_key = user_settings.cover_api_key or ""
    base_url = resolve_image_api_base_url(
        provider=user_settings.cover_api_provider,
        base_url=user_settings.cover_api_base_url,
        model=user_settings.cover_image_model,
    )
    model = user_settings.cover_image_model or ""
    provider = user_settings.cover_api_provider or ""
    image_text_language = normalize_image_text_language(getattr(user_settings, "image_text_language", None))
    if not api_key or not model:
        raise HTTPException(status_code=400, detail="图片生成配置不完整，请在设置页填写封面图片的 API Key 和模型")
    profile = resolve_image_provider_profile(provider=provider, base_url=base_url, model=model)
    return {
        "api_key": api_key,
        "base_url": base_url,
        "model": model,
        "provider": provider,
        "provider_profile": profile.as_dict(),
        "image_text_language": image_text_language,
    }


async def _call_image_api(
    prompt: str,
    *,
    api_key: str,
    base_url: str,
    model: str,
    seed: int | None = None,
    provider_profile: dict[str, Any] | None = None,
) -> tuple[bytes, Optional[str]]:
    configured_base_url = base_url.rstrip("/")
    if not configured_base_url or not api_key:
        raise HTTPException(status_code=400, detail="图片接口未配置，请在设置页配置封面图片的 API Key 和 API 地址")

    payload = build_image_generation_payload(
        prompt,
        model=model,
        size=IMAGE_SIZE,
        seed=seed,
        provider_profile=provider_profile,
    )
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    base_candidates = normalize_image_api_base_urls(configured_base_url)
    logger.info("角色形象图使用图片接口候选路径: %s", base_candidates)

    timeout = httpx.Timeout(600.0, connect=15.0)
    last_exc: ImageGenerationError | None = None
    async with httpx.AsyncClient(timeout=timeout) as client:
        for base_url in base_candidates or [configured_base_url]:
            try:
                response = await client.post(f"{base_url}/images/generations", json=payload, headers=headers)
                response.raise_for_status()
                break
            except httpx.HTTPStatusError as exc:
                detail = _extract_error_detail(exc)
                error_type, mapped_status = _classify_generation_error(exc.response.status_code, detail)
                last_exc = ImageGenerationError(error_type=error_type, detail=detail, status_code=mapped_status)
                if exc.response.status_code == 404 and base_url != base_candidates[-1]:
                    logger.warning("图片接口路径 404，尝试下一个候选路径: %s", base_url)
                    continue
                raise last_exc from exc
            except httpx.TimeoutException as exc:
                raise ImageGenerationError(error_type="capacity", detail="图片接口响应超时，请稍后重试", status_code=503) from exc
            except httpx.HTTPError as exc:
                raise ImageGenerationError(error_type="failed", detail=str(exc), status_code=502) from exc
        else:
            if last_exc:
                raise last_exc
            raise ImageGenerationError(error_type="failed", detail="图片接口候选路径全部不可用", status_code=502)

    try:
        data = response.json()
    except json.JSONDecodeError as exc:
        raise ImageGenerationError(error_type="failed", detail="图片接口返回了无效 JSON", status_code=502) from exc

    try:
        return decode_b64_image_response(data)
    except ValueError as exc:
        raise ImageGenerationError(error_type="failed", detail=str(exc), status_code=502) from exc


async def _call_image_edit_api(
    prompt: str,
    image_bytes: bytes,
    *,
    api_key: str,
    base_url: str,
    model: str,
    provider_profile: dict[str, Any] | None = None,
) -> tuple[bytes, Optional[str]]:
    configured_base_url = base_url.rstrip("/")
    if not configured_base_url or not api_key:
        raise HTTPException(status_code=400, detail="图片接口未配置，请在设置页配置封面图片的 API Key 和 API 地址")

    headers = {
        "Authorization": f"Bearer {api_key}",
    }

    base_candidates = normalize_image_api_base_urls(configured_base_url)
    logger.info("角色形象图改图使用图片接口候选路径: %s", base_candidates)

    timeout = httpx.Timeout(600.0, connect=15.0)
    last_exc: ImageGenerationError | None = None
    async with httpx.AsyncClient(timeout=timeout) as client:
        for base_url in base_candidates or [configured_base_url]:
            try:
                files = {
                    "image": ("image.png", image_bytes, "image/png"),
                }
                form_data = build_image_edit_payload(
                    prompt,
                    model=model,
                    size=IMAGE_SIZE,
                    provider_profile=provider_profile,
                )
                response = await client.post(
                    f"{base_url}/images/edits",
                    files=files,
                    data=form_data,
                    headers=headers,
                )
                response.raise_for_status()
                break
            except httpx.HTTPStatusError as exc:
                detail = _extract_error_detail(exc)
                error_type, mapped_status = _classify_generation_error(exc.response.status_code, detail)
                last_exc = ImageGenerationError(error_type=error_type, detail=detail, status_code=mapped_status)
                if exc.response.status_code == 404 and base_url != base_candidates[-1]:
                    logger.warning("角色形象图改图接口路径 404，尝试下一个候选路径: %s", base_url)
                    continue
                raise last_exc from exc
            except httpx.TimeoutException as exc:
                raise ImageGenerationError(error_type="capacity", detail="图片接口响应超时，请稍后重试", status_code=503) from exc
            except httpx.HTTPError as exc:
                raise ImageGenerationError(error_type="failed", detail=str(exc), status_code=502) from exc
        else:
            if last_exc:
                raise last_exc
            raise ImageGenerationError(error_type="failed", detail="图片接口候选路径全部不可用", status_code=502)

    try:
        data = response.json()
    except json.JSONDecodeError as exc:
        raise ImageGenerationError(error_type="failed", detail="图片接口返回了无效 JSON", status_code=502) from exc

    try:
        return decode_b64_image_response(data)
    except ValueError as exc:
        raise ImageGenerationError(error_type="failed", detail=str(exc), status_code=502) from exc


async def _generate_image_with_retry(
    raw_prompt: str,
    *,
    api_key: str,
    base_url: str,
    model: str,
    seed: int | None = None,
    gender: Optional[str] = None,
    is_organization: bool = False,
    provider_profile: dict[str, Any] | None = None,
) -> tuple[bytes, Optional[str]]:
    prompt = _ensure_prompt_suffix(raw_prompt, gender=gender, is_organization=is_organization)
    softened_prompt = _ensure_prompt_suffix(
        _sanitize_prompt_text(prompt),
        gender=gender,
        is_organization=is_organization,
    )
    capacity_retry_count = 0
    policy_retry_used = False
    current_prompt = prompt

    while True:
        try:
            return await _call_image_api(
                current_prompt,
                api_key=api_key,
                base_url=base_url,
                model=model,
                seed=seed,
                provider_profile=provider_profile,
            )
        except ImageGenerationError as exc:
            if exc.error_type == "capacity" and capacity_retry_count < 2:
                capacity_retry_count += 1
                wait_seconds = 2 ** capacity_retry_count
                logger.warning("角色形象图遇到容量波动，准备重试: wait=%ss detail=%s", wait_seconds, exc.detail)
                await asyncio.sleep(wait_seconds)
                continue

            if exc.error_type == "policy" and not policy_retry_used and softened_prompt != current_prompt:
                policy_retry_used = True
                current_prompt = softened_prompt
                logger.info("角色形象图提示词触发策略，已使用弱化提示词重试一次")
                continue

            raise


async def _edit_image_with_retry(
    raw_prompt: str,
    image_bytes: bytes,
    *,
    api_key: str,
    base_url: str,
    model: str,
    provider_profile: dict[str, Any] | None = None,
) -> tuple[bytes, Optional[str]]:
    capacity_retry_count = 0
    policy_retry_used = False
    current_prompt = raw_prompt
    softened_prompt = _sanitize_prompt_text(raw_prompt)

    while True:
        try:
            return await _call_image_edit_api(
                current_prompt,
                image_bytes,
                api_key=api_key,
                base_url=base_url,
                model=model,
                provider_profile=provider_profile,
            )
        except ImageGenerationError as exc:
            if exc.error_type == "capacity" and capacity_retry_count < 2:
                capacity_retry_count += 1
                wait_seconds = 2 ** capacity_retry_count
                logger.warning("角色形象图改图遇到容量波动，准备重试: wait=%ss detail=%s", wait_seconds, exc.detail)
                await asyncio.sleep(wait_seconds)
                continue

            if exc.error_type == "policy" and not policy_retry_used and softened_prompt != current_prompt:
                policy_retry_used = True
                current_prompt = softened_prompt
                logger.info("角色形象图改图提示词触发策略，已使用弱化提示词重试一次")
                continue

            raise


def _role_priority(character: Character) -> tuple[int, datetime]:
    role_rank = {
        "protagonist": 0,
        "supporting": 1,
        "antagonist": 2,
    }.get(character.role_type or "", 3)
    created_at = character.created_at or datetime.min
    return (role_rank, created_at)


async def _get_character_artifact_map(project_id: str, db: AsyncSession) -> dict[str, CharacterImageArtifact]:
    result = await db.execute(
        select(CharacterImageArtifact).where(CharacterImageArtifact.project_id == project_id)
    )
    return {artifact.character_id: artifact for artifact in result.scalars().all()}


def _apply_artifact_entry(
    artifact: CharacterImageArtifact,
    character: Character,
    entry: dict,
) -> bool:
    changed = False
    field_values = {
        "project_id": character.project_id,
        "character_id": character.id,
        "prompt": entry.get("prompt") or "",
        "status": entry.get("status") or "none",
        "error": entry.get("error"),
        "error_type": entry.get("error_type"),
        "file_name": entry.get("file_name"),
        "local_path": entry.get("local_path"),
        "cos_bucket": entry.get("cos_bucket"),
        "cos_region": entry.get("cos_region"),
        "cos_object_key": entry.get("cos_object_key"),
        "cos_url": entry.get("cos_url"),
        "content_type": entry.get("content_type"),
        "content_length": entry.get("content_length"),
    }
    for field_name, value in field_values.items():
        if getattr(artifact, field_name) != value:
            setattr(artifact, field_name, value)
            changed = True
    if changed:
        artifact.updated_at = _utc_now()
    return changed


def _merge_cos_metadata(entry: dict, metadata: COSObjectMetadata | None) -> None:
    if not metadata:
        return
    entry["cos_bucket"] = metadata.bucket
    entry["cos_region"] = metadata.region
    entry["cos_object_key"] = metadata.object_key
    entry["cos_url"] = metadata.url
    entry["content_type"] = metadata.content_type
    entry["content_length"] = metadata.content_length


def _serialize_state_entry(entry: dict[str, Any]) -> dict[str, Any]:
    return _strip_has_image(entry)


def _select_preview_variant(variants: dict[str, dict[str, Any]]) -> dict[str, Any]:
    if DEFAULT_VARIANT_KEY in variants:
        return variants[DEFAULT_VARIANT_KEY]
    return sorted(variants.values(), key=_variant_sort_key)[0]


def _variant_matches_chapter(entry: dict[str, Any], chapter_number: int) -> bool:
    chapter_start = entry.get("chapter_start")
    chapter_end = entry.get("chapter_end")
    if chapter_start is None and chapter_end is None:
        return False
    if chapter_start is not None and chapter_number < int(chapter_start):
        return False
    if chapter_end is not None and chapter_number > int(chapter_end):
        return False
    return True


def _select_variant_for_chapter(variants: dict[str, dict[str, Any]], chapter_number: int) -> dict[str, Any]:
    matched = [
        entry for entry in variants.values()
        if entry.get("variant_key") != DEFAULT_VARIANT_KEY and _variant_matches_chapter(entry, chapter_number)
    ]
    if matched:
        matched.sort(
            key=lambda entry: (
                int((entry.get("chapter_end") or chapter_number) - (entry.get("chapter_start") or chapter_number)),
                int(entry.get("sort_order") or 0),
                str(entry.get("variant_label") or ""),
            )
        )
        return matched[0]
    return _select_preview_variant(variants)


async def _sync_project_state(
    project: Project,
    characters: list[Character],
    db: AsyncSession,
    *,
    prune_missing: bool = False,
) -> dict[str, dict[str, dict[str, Any]]]:
    project_id = project.id
    lock = await _get_state_lock(project_id)
    async with lock:
        state = _read_state_from_disk(project_id)
        artifacts = await _get_character_artifact_map(project_id, db)
        current_ids = {character.id for character in characters}
        state_changed = False
        db_changed = False

        if prune_missing:
            for stale_id in list(state.keys()):
                if stale_id not in current_ids:
                    state.pop(stale_id, None)
                    state_changed = True

            for stale_id, artifact in list(artifacts.items()):
                if stale_id not in current_ids:
                    await db.delete(artifact)
                    artifacts.pop(stale_id, None)
                    db_changed = True

        normalized_state: dict[str, dict[str, dict[str, Any]]] = {}
        for character in characters:
            fallback_variants = state.get(character.id) or {}
            artifact = artifacts.get(character.id)
            default_fallback = fallback_variants.get(DEFAULT_VARIANT_KEY) or {}
            default_entry = _artifact_to_entry(artifact, default_fallback, character)
            if not default_entry["prompt"]:
                default_entry["prompt"] = _build_default_prompt(project, character)
            if default_entry["status"] == "ready" and not default_entry["has_image"]:
                default_entry["status"] = "none"
                default_entry["error"] = "图片文件缺失，请重新生成"
                default_entry["error_type"] = "missing_file"
            artifact_updated_at = _datetime_to_iso(artifact.updated_at) if artifact else None
            default_entry["updated_at"] = default_entry.get("updated_at") or artifact_updated_at

            if artifact is None:
                artifact = CharacterImageArtifact(
                    project_id=character.project_id,
                    character_id=character.id,
                )
                db.add(artifact)
                artifacts[character.id] = artifact
                db_changed = True
            if _apply_artifact_entry(artifact, character, default_entry):
                db_changed = True

            character_variants: dict[str, dict[str, Any]] = {
                DEFAULT_VARIANT_KEY: default_entry,
            }
            for variant_key, variant_entry in fallback_variants.items():
                if variant_key == DEFAULT_VARIANT_KEY:
                    continue
                normalized_entry = _normalize_variant_state_entry(project, character, variant_key, variant_entry)
                if normalized_entry["status"] == "ready" and not normalized_entry["has_image"]:
                    normalized_entry["status"] = "none"
                    normalized_entry["error"] = "图片文件缺失，请重新生成"
                    normalized_entry["error_type"] = "missing_file"
                character_variants[variant_key] = normalized_entry

            normalized_state[character.id] = character_variants
            state[character.id] = {
                variant_key: _serialize_state_entry(entry)
                for variant_key, entry in character_variants.items()
            }

        if db_changed:
            await db.commit()
            for character in characters:
                artifact = artifacts.get(character.id)
                if artifact:
                    default_entry = normalized_state[character.id][DEFAULT_VARIANT_KEY]
                    default_entry["updated_at"] = _datetime_to_iso(artifact.updated_at)
                    state[character.id][DEFAULT_VARIANT_KEY]["updated_at"] = _datetime_to_iso(artifact.updated_at)

        if state_changed or db_changed:
            _write_state_to_disk(project_id, state)
        return normalized_state


async def _load_character_and_project(
    character_id: str,
    request: Request,
    db: AsyncSession,
) -> tuple[Character, Project]:
    result = await db.execute(select(Character).where(Character.id == character_id))
    character = result.scalar_one_or_none()
    if not character:
        raise HTTPException(status_code=404, detail="角色不存在")

    user_id = getattr(request.state, "user_id", None)
    project = await verify_project_access(character.project_id, user_id, db)
    return character, project


async def _get_or_create_artifact(character: Character, db: AsyncSession) -> CharacterImageArtifact:
    result = await db.execute(
        select(CharacterImageArtifact).where(CharacterImageArtifact.character_id == character.id)
    )
    artifact = result.scalar_one_or_none()
    if artifact:
        return artifact
    artifact = CharacterImageArtifact(project_id=character.project_id, character_id=character.id, prompt="")
    db.add(artifact)
    await db.flush()
    return artifact


async def _upload_image_to_cos(
    *,
    project: Project,
    character: Character,
    variant_key: str,
    file_name: str,
    image_bytes: bytes,
) -> COSObjectMetadata | None:
    _require_cos_image_storage()
    object_key = tencent_cos_storage.build_object_key("character-images", project.id, file_name)
    return await tencent_cos_storage.upload_bytes(
        object_key=object_key,
        content=image_bytes,
        content_type="image/png",
    )


async def _persist_default_artifact_entry(
    *,
    character: Character,
    entry: dict[str, Any],
    db: AsyncSession,
) -> None:
    artifact = await _get_or_create_artifact(character, db)
    _apply_artifact_entry(artifact, character, entry)
    await db.commit()


def _write_variant_state(
    *,
    project_id: str,
    character_id: str,
    variant_key: str,
    entry: dict[str, Any],
) -> None:
    state = _read_state_from_disk(project_id)
    character_state = state.setdefault(character_id, {})
    character_state[variant_key] = _serialize_state_entry(entry)
    _write_state_to_disk(project_id, state)


def _delete_variant_state(
    *,
    project_id: str,
    character_id: str,
    variant_key: str,
) -> None:
    state = _read_state_from_disk(project_id)
    character_state = state.get(character_id, {})
    if variant_key in character_state:
        character_state.pop(variant_key, None)
        if character_state:
            state[character_id] = character_state
        else:
            state.pop(character_id, None)
        _write_state_to_disk(project_id, state)


async def _generate_for_variant(
    *,
    project: Project,
    character: Character,
    variant_key: str,
    overwrite: bool,
    db: AsyncSession,
) -> tuple[CharacterImageActionResponse, bool]:
    _require_cos_image_storage()
    state = await _sync_project_state(project, [character], db)
    variants = state[character.id]
    if variant_key not in variants:
        raise HTTPException(status_code=404, detail="形象版本不存在")
    current_entry = dict(variants[variant_key])
    if current_entry.get("has_image") and not overwrite:
        response = _build_state_response(character, current_entry, variant_count=len(variants))
        return CharacterImageActionResponse(**response.model_dump(), message="当前角色已存在形象图，已跳过"), False

    lock = await _get_state_lock(project.id)
    async with lock:
        prompt_to_use = current_entry.get("prompt") or _build_default_prompt(project, character)
        current_entry.update(
            {
                "prompt": prompt_to_use,
                "status": "generating",
                "updated_at": _utc_now_iso(),
                "error": None,
                "error_type": None,
            }
        )
        if variant_key == DEFAULT_VARIANT_KEY:
            await _persist_default_artifact_entry(character=character, entry=current_entry, db=db)
        _write_variant_state(project_id=project.id, character_id=character.id, variant_key=variant_key, entry=current_entry)

    try:
        user_settings = await _load_image_settings(project.user_id, db)
        provider_profile = user_settings.get("provider_profile") or {}
        prompt_for_generation = append_visible_text_rule(
            _append_project_comic_style(prompt_to_use, project),
            user_settings.get("image_text_language"),
        )
        generation_plan = CharacterImageWorkflowAgent.prepare_variant_generation(
            project_id=project.id,
            character_id=character.id,
            variant_key=variant_key,
            prompt_text=prompt_for_generation,
            provider_profile=provider_profile,
        )
        image_bytes, revised_prompt = await _generate_image_with_retry(
            prompt_for_generation,
            api_key=user_settings["api_key"],
            base_url=user_settings["base_url"],
            model=user_settings["model"],
            seed=generation_plan["seed"],
            gender=character.gender,
            is_organization=bool(character.is_organization),
            provider_profile=provider_profile,
        )
    except ImageGenerationError as exc:
        lock = await _get_state_lock(project.id)
        async with lock:
            current_entry.update(
                {
                    "prompt": current_entry.get("prompt") or prompt_to_use,
                    "status": exc.error_type,
                    "updated_at": _utc_now_iso(),
                    "error": exc.detail,
                    "error_type": exc.error_type,
                    "consistency_key": current_entry.get("consistency_key"),
                }
            )
            if variant_key == DEFAULT_VARIANT_KEY:
                await _persist_default_artifact_entry(character=character, entry=current_entry, db=db)
            _write_variant_state(project_id=project.id, character_id=character.id, variant_key=variant_key, entry=current_entry)
            response = _build_state_response(character, dict(current_entry, has_image=_entry_has_image(current_entry)), variant_count=len(variants))

        message = {
            "capacity": "图片接口暂时繁忙，已按退避重试，仍未成功",
            "policy": "提示词触发图片策略，已自动弱化并重试一次，仍未成功",
        }.get(exc.error_type, "角色形象图生成失败")
        return CharacterImageActionResponse(**response.model_dump(), message=message), True

    next_revision = _next_image_revision(current_entry)
    file_name = _new_variant_image_file(project.id, character.id, variant_key, next_revision).name
    cos_metadata = await _upload_image_to_cos(
        project=project,
        character=character,
        variant_key=variant_key,
        file_name=file_name,
        image_bytes=image_bytes,
    )

    lock = await _get_state_lock(project.id)
    async with lock:
        current_entry.update(
            {
                "prompt": prompt_to_use,
                "status": "ready",
                "updated_at": _utc_now_iso(),
                "error": None,
                "error_type": None,
                "file_name": file_name,
                "local_path": None,
                "content_type": "image/png",
                "content_length": len(image_bytes),
                "image_revision": next_revision,
                "consistency_key": generation_plan["consistency_key"],
            }
        )
        _merge_cos_metadata(current_entry, cos_metadata)
        if variant_key == DEFAULT_VARIANT_KEY:
            await _persist_default_artifact_entry(character=character, entry=current_entry, db=db)
            current_entry["updated_at"] = _datetime_to_iso((await _get_or_create_artifact(character, db)).updated_at)
        _write_variant_state(project_id=project.id, character_id=character.id, variant_key=variant_key, entry=current_entry)
        response = _build_state_response(character, dict(current_entry, has_image=True), variant_count=len(variants))

    message = "角色形象图生成成功"
    return CharacterImageActionResponse(**response.model_dump(), message=message), True


async def _generate_for_character(
    *,
    project: Project,
    character: Character,
    overwrite: bool,
    db: AsyncSession,
) -> tuple[CharacterImageActionResponse, bool]:
    return await _generate_for_variant(
        project=project,
        character=character,
        variant_key=DEFAULT_VARIANT_KEY,
        overwrite=overwrite,
        db=db,
    )


async def _edit_for_variant(
    *,
    project: Project,
    character: Character,
    variant_key: str,
    edit_prompt: str,
    db: AsyncSession,
) -> tuple[CharacterImageActionResponse, bool]:
    _require_cos_image_storage()
    state = await _sync_project_state(project, [character], db)
    variants = state[character.id]
    if variant_key not in variants:
        raise HTTPException(status_code=404, detail="形象版本不存在")
    current_entry = dict(variants[variant_key])
    if not current_entry.get("has_image"):
        raise HTTPException(status_code=400, detail="当前版本还没有形象图，请先生成形象图")
    original_prompt = current_entry.get("prompt") or _build_default_prompt(project, character)
    current_entry["prompt"] = original_prompt

    # 读取原图
    original_image_bytes: Optional[bytes] = None
    cos_object_key = current_entry.get("cos_object_key")
    if cos_object_key:
        try:
            original_image_bytes, _ = await tencent_cos_storage.download_bytes(object_key=cos_object_key)
        except Exception:
            logger.warning("从 COS 读取原图失败: character_id=%s", character.id, exc_info=True)
    if not original_image_bytes:
        raise HTTPException(status_code=400, detail="无法读取原图文件，请先生成形象图")

    lock = await _get_state_lock(project.id)
    async with lock:
        current_entry.update(
            {
                "status": "generating",
                "updated_at": _utc_now_iso(),
                "error": None,
                "error_type": None,
            }
        )
        if variant_key == DEFAULT_VARIANT_KEY:
            await _persist_default_artifact_entry(character=character, entry=current_entry, db=db)
        _write_variant_state(project_id=project.id, character_id=character.id, variant_key=variant_key, entry=current_entry)

    try:
        user_settings = await _load_image_settings(project.user_id, db)
        provider_profile = user_settings.get("provider_profile") or {}
        edit_model = resolve_image_edit_model(user_settings["model"], provider_profile=provider_profile)
        image_bytes, revised_prompt = await _edit_image_with_retry(
            _append_project_comic_style(edit_prompt, project),
            original_image_bytes,
            api_key=user_settings["api_key"],
            base_url=user_settings["base_url"],
            model=edit_model,
            provider_profile=provider_profile,
        )
    except ImageGenerationError as exc:
        lock = await _get_state_lock(project.id)
        async with lock:
            current_entry.update(
                {
                    "prompt": original_prompt,
                    "status": exc.error_type,
                    "updated_at": _utc_now_iso(),
                    "error": exc.detail,
                    "error_type": exc.error_type,
                }
            )
            if variant_key == DEFAULT_VARIANT_KEY:
                await _persist_default_artifact_entry(character=character, entry=current_entry, db=db)
            _write_variant_state(project_id=project.id, character_id=character.id, variant_key=variant_key, entry=current_entry)
            response = _build_state_response(character, dict(current_entry, has_image=_entry_has_image(current_entry)), variant_count=len(variants))

        message = {
            "capacity": "图片接口暂时繁忙，已按退避重试，仍未成功",
            "policy": "提示词触发图片策略，已自动弱化并重试一次，仍未成功",
        }.get(exc.error_type, "角色形象图改图失败")
        return CharacterImageActionResponse(**response.model_dump(), message=message), True

    next_revision = _next_image_revision(current_entry)
    file_name = _new_variant_image_file(project.id, character.id, variant_key, next_revision).name
    cos_metadata = await _upload_image_to_cos(
        project=project,
        character=character,
        variant_key=variant_key,
        file_name=file_name,
        image_bytes=image_bytes,
    )

    lock = await _get_state_lock(project.id)
    async with lock:
        current_entry.update(
            {
                "status": "ready",
                "updated_at": _utc_now_iso(),
                "error": None,
                "error_type": None,
                "file_name": file_name,
                "local_path": None,
                "content_type": "image/png",
                "content_length": len(image_bytes),
                "image_revision": next_revision,
            }
        )
        _merge_cos_metadata(current_entry, cos_metadata)
        if variant_key == DEFAULT_VARIANT_KEY:
            await _persist_default_artifact_entry(character=character, entry=current_entry, db=db)
            current_entry["updated_at"] = _datetime_to_iso((await _get_or_create_artifact(character, db)).updated_at)
        _write_variant_state(project_id=project.id, character_id=character.id, variant_key=variant_key, entry=current_entry)
        response = _build_state_response(character, dict(current_entry, has_image=True), variant_count=len(variants))

    message = "角色形象图改图成功"
    return CharacterImageActionResponse(**response.model_dump(), message=message), True


async def _mark_variant_generation_queued(
    *,
    project: Project,
    character: Character,
    variant_key: str,
    overwrite: bool,
    db: AsyncSession,
) -> CharacterImageActionResponse:
    _require_cos_image_storage()
    state = await _sync_project_state(project, [character], db)
    variants = state[character.id]
    if variant_key not in variants:
        raise HTTPException(status_code=404, detail="形象版本不存在")
    current_entry = dict(variants[variant_key])
    if current_entry.get("has_image") and not overwrite:
        response = _build_state_response(character, current_entry, variant_count=len(variants))
        return CharacterImageActionResponse(**response.model_dump(), message="当前角色已存在形象图，已跳过", queued=False)

    prompt_to_use = current_entry.get("prompt") or _build_default_prompt(project, character)
    current_entry.update(
        {
            "prompt": prompt_to_use,
            "status": "generating",
            "updated_at": _utc_now_iso(),
            "error": None,
            "error_type": None,
        }
    )
    lock = await _get_state_lock(project.id)
    async with lock:
        if variant_key == DEFAULT_VARIANT_KEY:
            await _persist_default_artifact_entry(character=character, entry=current_entry, db=db)
        _write_variant_state(project_id=project.id, character_id=character.id, variant_key=variant_key, entry=current_entry)

    response = _build_state_response(character, dict(current_entry, has_image=_entry_has_image(current_entry)), variant_count=len(variants))
    return CharacterImageActionResponse(**response.model_dump(), message="角色形象图生成任务已加入后台队列", task_id=str(uuid.uuid4()), queued=True)


async def _should_retry_variant_generation(
    *,
    project: Project,
    character: Character,
    variant_key: str,
    db: AsyncSession,
) -> tuple[bool, str]:
    state = await _sync_project_state(project, [character], db)
    variants = state.get(character.id) or {}
    entry = variants.get(variant_key) or {}
    status = str(entry.get("status") or "")
    error_text = " ".join(
        str(value or "")
        for value in (entry.get("error"), entry.get("error_type"))
    ).lower()
    retry_hints = (
        "timeout",
        "timed out",
        "502",
        "503",
        "504",
        "bad gateway",
        "service unavailable",
        "gateway timeout",
        "upstream",
        "capacity",
        "no available compatible accounts",
        "图片接口响应超时",
        "图片接口暂时繁忙",
    )
    if status == "capacity":
        return True, error_text or status
    if any(hint in error_text for hint in retry_hints):
        return True, error_text or status
    return False, error_text or status


async def _generate_for_variant_with_background_retries(
    *,
    project: Project,
    character: Character,
    variant_key: str,
    overwrite: bool,
    db: AsyncSession,
) -> None:
    max_attempts = 3
    retry_delays = (20, 60)
    for attempt in range(1, max_attempts + 1):
        logger.info(
            "后台角色形象图生成开始: project_id=%s character_id=%s variant_key=%s attempt=%s/%s",
            project.id,
            character.id,
            variant_key,
            attempt,
            max_attempts,
        )
        try:
            response, _ = await _generate_for_variant(
                project=project,
                character=character,
                variant_key=variant_key,
                overwrite=overwrite,
                db=db,
            )
        except Exception as exc:
            error_text = str(exc).lower()
            retryable = any(
                hint in error_text
                for hint in (
                    "timeout",
                    "timed out",
                    "502",
                    "503",
                    "504",
                    "bad gateway",
                    "service unavailable",
                    "gateway timeout",
                    "upstream",
                    "capacity",
                    "no available compatible accounts",
                )
            )
            if retryable and attempt < max_attempts:
                delay = retry_delays[attempt - 1]
                logger.warning(
                    "后台角色形象图生成异常，将重试: project_id=%s character_id=%s variant_key=%s attempt=%s/%s delay=%ss error=%s",
                    project.id,
                    character.id,
                    variant_key,
                    attempt,
                    max_attempts,
                    delay,
                    exc,
                )
                await asyncio.sleep(delay)
                continue
            raise

        if response.status == "ready":
            logger.info(
                "后台角色形象图生成成功: project_id=%s character_id=%s variant_key=%s attempt=%s/%s",
                project.id,
                character.id,
                variant_key,
                attempt,
                max_attempts,
            )
            return
        if response.status == "policy":
            logger.info(
                "后台角色形象图生成遇到策略错误，不重试: project_id=%s character_id=%s variant_key=%s attempt=%s/%s error=%s",
                project.id,
                character.id,
                variant_key,
                attempt,
                max_attempts,
                response.error,
            )
            return

        retryable, reason = await _should_retry_variant_generation(
            project=project,
            character=character,
            variant_key=variant_key,
            db=db,
        )
        if retryable and attempt < max_attempts:
            delay = retry_delays[attempt - 1]
            logger.warning(
                "后台角色形象图生成未成功，将重试: project_id=%s character_id=%s variant_key=%s attempt=%s/%s delay=%ss status=%s reason=%s",
                project.id,
                character.id,
                variant_key,
                attempt,
                max_attempts,
                delay,
                response.status,
                reason,
            )
            await asyncio.sleep(delay)
            continue
        logger.info(
            "后台角色形象图生成结束: project_id=%s character_id=%s variant_key=%s attempt=%s/%s status=%s reason=%s",
            project.id,
            character.id,
            variant_key,
            attempt,
            max_attempts,
            response.status,
            reason,
        )
        return


async def _run_variant_generation_task(
    *,
    user_id: str,
    project_id: str,
    character_id: str,
    variant_key: str,
    overwrite: bool,
) -> None:
    try:
        engine = await get_engine(user_id)
        SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with SessionLocal() as db:
            result = await db.execute(select(Character).where(Character.id == character_id))
            character = result.scalar_one_or_none()
            if not character:
                logger.warning("后台角色形象图任务找不到角色: character_id=%s", character_id)
                return
            result = await db.execute(select(Project).where(Project.id == project_id))
            project = result.scalar_one_or_none()
            if not project:
                logger.warning("后台角色形象图任务找不到项目: project_id=%s", project_id)
                return
            await _generate_for_variant_with_background_retries(
                project=project,
                character=character,
                variant_key=variant_key,
                overwrite=overwrite,
                db=db,
            )
    except Exception:
        logger.error(
            "后台角色形象图生成任务异常: project_id=%s character_id=%s variant_key=%s",
            project_id,
            character_id,
            variant_key,
            exc_info=True,
        )


async def _mark_variant_edit_queued(
    *,
    project: Project,
    character: Character,
    variant_key: str,
    edit_prompt: str,
    db: AsyncSession,
) -> CharacterImageActionResponse:
    _require_cos_image_storage()
    state = await _sync_project_state(project, [character], db)
    variants = state[character.id]
    if variant_key not in variants:
        raise HTTPException(status_code=404, detail="形象版本不存在")
    current_entry = dict(variants[variant_key])
    if not current_entry.get("has_image"):
        raise HTTPException(status_code=400, detail="当前版本还没有形象图，请先生成形象图")
    original_prompt = current_entry.get("prompt") or _build_default_prompt(project, character)

    current_entry.update(
        {
            "prompt": original_prompt,
            "status": "generating",
            "updated_at": _utc_now_iso(),
            "error": None,
            "error_type": None,
        }
    )
    lock = await _get_state_lock(project.id)
    async with lock:
        if variant_key == DEFAULT_VARIANT_KEY:
            await _persist_default_artifact_entry(character=character, entry=current_entry, db=db)
        _write_variant_state(project_id=project.id, character_id=character.id, variant_key=variant_key, entry=current_entry)

    response = _build_state_response(character, dict(current_entry, has_image=_entry_has_image(current_entry)), variant_count=len(variants))
    return CharacterImageActionResponse(**response.model_dump(), message="角色形象图改图任务已加入后台队列", task_id=str(uuid.uuid4()), queued=True)


async def _edit_for_variant_with_background_retries(
    *,
    project: Project,
    character: Character,
    variant_key: str,
    edit_prompt: str,
    db: AsyncSession,
) -> None:
    max_attempts = 3
    retry_delays = (20, 60)
    for attempt in range(1, max_attempts + 1):
        logger.info(
            "后台角色形象图改图开始: project_id=%s character_id=%s variant_key=%s attempt=%s/%s",
            project.id,
            character.id,
            variant_key,
            attempt,
            max_attempts,
        )
        try:
            response, _ = await _edit_for_variant(
                project=project,
                character=character,
                variant_key=variant_key,
                edit_prompt=edit_prompt,
                db=db,
            )
        except Exception as exc:
            error_text = str(exc).lower()
            retryable = any(
                hint in error_text
                for hint in (
                    "timeout",
                    "timed out",
                    "502",
                    "503",
                    "504",
                    "bad gateway",
                    "service unavailable",
                    "gateway timeout",
                    "upstream",
                    "capacity",
                    "no available compatible accounts",
                )
            )
            if retryable and attempt < max_attempts:
                delay = retry_delays[attempt - 1]
                logger.warning(
                    "后台角色形象图改图异常，将重试: project_id=%s character_id=%s variant_key=%s attempt=%s/%s delay=%ss error=%s",
                    project.id,
                    character.id,
                    variant_key,
                    attempt,
                    max_attempts,
                    delay,
                    exc,
                )
                await asyncio.sleep(delay)
                continue
            raise

        if response.status == "ready":
            logger.info(
                "后台角色形象图改图成功: project_id=%s character_id=%s variant_key=%s attempt=%s/%s",
                project.id,
                character.id,
                variant_key,
                attempt,
                max_attempts,
            )
            return
        if response.status == "policy":
            logger.info(
                "后台角色形象图改图遇到策略错误，不重试: project_id=%s character_id=%s variant_key=%s attempt=%s/%s error=%s",
                project.id,
                character.id,
                variant_key,
                attempt,
                max_attempts,
                response.error,
            )
            return

        retryable, reason = await _should_retry_variant_generation(
            project=project,
            character=character,
            variant_key=variant_key,
            db=db,
        )
        if retryable and attempt < max_attempts:
            delay = retry_delays[attempt - 1]
            logger.warning(
                "后台角色形象图改图未成功，将重试: project_id=%s character_id=%s variant_key=%s attempt=%s/%s delay=%ss status=%s reason=%s",
                project.id,
                character.id,
                variant_key,
                attempt,
                max_attempts,
                delay,
                response.status,
                reason,
            )
            await asyncio.sleep(delay)
            continue
        logger.info(
            "后台角色形象图改图结束: project_id=%s character_id=%s variant_key=%s attempt=%s/%s status=%s reason=%s",
            project.id,
            character.id,
            variant_key,
            attempt,
            max_attempts,
            response.status,
            reason,
        )
        return


async def _run_variant_edit_task(
    *,
    user_id: str,
    project_id: str,
    character_id: str,
    variant_key: str,
    edit_prompt: str,
) -> None:
    try:
        engine = await get_engine(user_id)
        SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with SessionLocal() as db:
            result = await db.execute(select(Character).where(Character.id == character_id))
            character = result.scalar_one_or_none()
            if not character:
                logger.warning("后台角色形象图改图任务找不到角色: character_id=%s", character_id)
                return
            result = await db.execute(select(Project).where(Project.id == project_id))
            project = result.scalar_one_or_none()
            if not project:
                logger.warning("后台角色形象图改图任务找不到项目: project_id=%s", project_id)
                return
            await _edit_for_variant_with_background_retries(
                project=project,
                character=character,
                variant_key=variant_key,
                edit_prompt=edit_prompt,
                db=db,
            )
    except Exception:
        logger.error(
            "后台角色形象图改图任务异常: project_id=%s character_id=%s variant_key=%s",
            project_id,
            character_id,
            variant_key,
            exc_info=True,
        )


@router.get("/projects/{project_id}", response_model=CharacterImageListResponse, summary="获取项目角色形象图状态")
async def get_project_character_images(
    project_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user_id = getattr(request.state, "user_id", None)
    project = await verify_project_access(project_id, user_id, db)
    result = await db.execute(
        select(Character)
        .where(Character.project_id == project_id)
        .order_by(Character.created_at.desc())
    )
    characters = result.scalars().all()
    state = await _sync_project_state(project, characters, db, prune_missing=True)
    items = [
        _build_state_response(
            character,
            _select_preview_variant(state[character.id]),
            variant_count=len(state[character.id]),
        )
        for character in characters
    ]
    return CharacterImageListResponse(project_id=project_id, total=len(items), items=items)


@router.get("/characters/{character_id}", response_model=CharacterImageStateResponse, summary="获取单个角色形象图状态")
async def get_character_image_state(
    character_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    character, project = await _load_character_and_project(character_id, request, db)
    state = await _sync_project_state(project, [character], db)
    variants = state[character.id]
    return _build_state_response(character, _select_preview_variant(variants), variant_count=len(variants))


@router.get("/characters/{character_id}/variants", response_model=CharacterImageVariantListResponse, summary="获取角色全部形象版本")
async def get_character_image_variants(
    character_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    character, project = await _load_character_and_project(character_id, request, db)
    state = await _sync_project_state(project, [character], db)
    variants = sorted(state[character.id].values(), key=_variant_sort_key)
    return CharacterImageVariantListResponse(
        character_id=character.id,
        project_id=character.project_id,
        name=character.name,
        total=len(variants),
        items=[_build_state_response(character, entry, variant_count=len(variants)) for entry in variants],
    )


@router.post("/characters/{character_id}/variants", response_model=CharacterImageActionResponse, summary="创建角色形象版本")
async def create_character_image_variant(
    character_id: str,
    payload: CharacterImageVariantCreateRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    character, project = await _load_character_and_project(character_id, request, db)
    _validate_chapter_scope(payload.chapter_start, payload.chapter_end)
    state = await _sync_project_state(project, [character], db)
    variants = state[character.id]
    variant_key = _new_variant_key(payload.variant_type)
    entry = _normalize_variant_state_entry(
        project,
        character,
        variant_key,
        {
            "variant_label": payload.variant_label,
            "variant_type": payload.variant_type,
            "chapter_start": payload.chapter_start,
            "chapter_end": payload.chapter_end,
            "sort_order": len(variants),
            "prompt": payload.prompt,
            "status": "none",
            "updated_at": _utc_now_iso(),
        },
    )
    lock = await _get_state_lock(project.id)
    async with lock:
        _write_variant_state(project_id=project.id, character_id=character.id, variant_key=variant_key, entry=entry)
    response = _build_state_response(character, entry, variant_count=len(variants) + 1)
    return CharacterImageActionResponse(**response.model_dump(), message="形象版本已创建")


@router.put("/characters/{character_id}/prompt", response_model=CharacterImageActionResponse, summary="保存角色形象提示词")
async def update_character_image_prompt(
    character_id: str,
    payload: CharacterImagePromptUpdateRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    character, project = await _load_character_and_project(character_id, request, db)
    state = await _sync_project_state(project, [character], db)
    variants = state[character.id]
    entry = dict(variants[DEFAULT_VARIANT_KEY])
    entry["prompt"] = payload.prompt.strip()
    entry["updated_at"] = _utc_now_iso()
    entry["status"] = entry.get("status") or "none"

    lock = await _get_state_lock(project.id)
    async with lock:
        await _persist_default_artifact_entry(character=character, entry=entry, db=db)
        artifact = await _get_or_create_artifact(character, db)
        entry["updated_at"] = _datetime_to_iso(artifact.updated_at)
        _write_variant_state(project_id=project.id, character_id=character.id, variant_key=DEFAULT_VARIANT_KEY, entry=entry)
        response = _build_state_response(character, dict(entry, has_image=_entry_has_image(entry)), variant_count=len(variants))
    return CharacterImageActionResponse(**response.model_dump(), message="形象提示词已保存")


@router.put("/characters/{character_id}/variants/{variant_key}", response_model=CharacterImageActionResponse, summary="更新角色形象版本")
async def update_character_image_variant(
    character_id: str,
    variant_key: str,
    payload: CharacterImageVariantUpdateRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    character, project = await _load_character_and_project(character_id, request, db)
    _validate_chapter_scope(payload.chapter_start, payload.chapter_end)
    state = await _sync_project_state(project, [character], db)
    variants = state[character.id]
    if variant_key not in variants:
        raise HTTPException(status_code=404, detail="形象版本不存在")
    entry = dict(variants[variant_key])
    if payload.variant_label is not None and variant_key != DEFAULT_VARIANT_KEY:
        entry["variant_label"] = _normalize_variant_label(payload.variant_label, payload.variant_type or entry.get("variant_type"))
    if payload.variant_type is not None and variant_key != DEFAULT_VARIANT_KEY:
        entry["variant_type"] = _normalize_variant_type(payload.variant_type)
    if payload.chapter_start is not None or payload.chapter_end is not None:
        entry["chapter_start"] = payload.chapter_start
        entry["chapter_end"] = payload.chapter_end
    if payload.prompt is not None:
        entry["prompt"] = payload.prompt.strip()
    entry["updated_at"] = _utc_now_iso()

    lock = await _get_state_lock(project.id)
    async with lock:
        if variant_key == DEFAULT_VARIANT_KEY:
            await _persist_default_artifact_entry(character=character, entry=entry, db=db)
            artifact = await _get_or_create_artifact(character, db)
            entry["updated_at"] = _datetime_to_iso(artifact.updated_at)
        _write_variant_state(project_id=project.id, character_id=character.id, variant_key=variant_key, entry=entry)
    response = _build_state_response(character, dict(entry, has_image=_entry_has_image(entry)), variant_count=len(variants))
    return CharacterImageActionResponse(**response.model_dump(), message="形象版本已更新")


@router.post("/characters/{character_id}/generate", response_model=CharacterImageActionResponse, summary="生成或重新生成角色形象图")
async def generate_character_image(
    character_id: str,
    payload: CharacterImageGenerateRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    character, project = await _load_character_and_project(character_id, request, db)
    response = await _mark_variant_generation_queued(
        project=project,
        character=character,
        variant_key=DEFAULT_VARIANT_KEY,
        overwrite=payload.overwrite,
        db=db,
    )
    if response.queued:
        user_id = getattr(request.state, "user_id", None)
        background_tasks.add_task(
            _run_variant_generation_task,
            user_id=user_id,
            project_id=project.id,
            character_id=character.id,
            variant_key=DEFAULT_VARIANT_KEY,
            overwrite=payload.overwrite,
        )
    return response


@router.post("/characters/{character_id}/variants/{variant_key}/generate", response_model=CharacterImageActionResponse, summary="生成指定角色形象版本")
async def generate_character_image_variant(
    character_id: str,
    variant_key: str,
    payload: CharacterImageGenerateRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    character, project = await _load_character_and_project(character_id, request, db)
    response = await _mark_variant_generation_queued(
        project=project,
        character=character,
        variant_key=variant_key,
        overwrite=payload.overwrite,
        db=db,
    )
    if response.queued:
        user_id = getattr(request.state, "user_id", None)
        background_tasks.add_task(
            _run_variant_generation_task,
            user_id=user_id,
            project_id=project.id,
            character_id=character.id,
            variant_key=variant_key,
            overwrite=payload.overwrite,
        )
    return response


@router.post("/characters/{character_id}/edit", response_model=CharacterImageActionResponse, summary="基于原图改图（默认版本）")
async def edit_character_image(
    character_id: str,
    payload: CharacterImageEditRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    character, project = await _load_character_and_project(character_id, request, db)
    response = await _mark_variant_edit_queued(
        project=project,
        character=character,
        variant_key=DEFAULT_VARIANT_KEY,
        edit_prompt=payload.prompt,
        db=db,
    )
    if response.queued:
        user_id = getattr(request.state, "user_id", None)
        background_tasks.add_task(
            _run_variant_edit_task,
            user_id=user_id,
            project_id=project.id,
            character_id=character.id,
            variant_key=DEFAULT_VARIANT_KEY,
            edit_prompt=payload.prompt,
        )
    return response


@router.post("/characters/{character_id}/variants/{variant_key}/edit", response_model=CharacterImageActionResponse, summary="基于原图改图（指定版本）")
async def edit_character_image_variant(
    character_id: str,
    variant_key: str,
    payload: CharacterImageEditRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    character, project = await _load_character_and_project(character_id, request, db)
    response = await _mark_variant_edit_queued(
        project=project,
        character=character,
        variant_key=variant_key,
        edit_prompt=payload.prompt,
        db=db,
    )
    if response.queued:
        user_id = getattr(request.state, "user_id", None)
        background_tasks.add_task(
            _run_variant_edit_task,
            user_id=user_id,
            project_id=project.id,
            character_id=character.id,
            variant_key=variant_key,
            edit_prompt=payload.prompt,
        )
    return response


@router.delete("/characters/{character_id}/variants/{variant_key}", summary="删除角色形象版本")
async def delete_character_image_variant(
    character_id: str,
    variant_key: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    if variant_key == DEFAULT_VARIANT_KEY:
        raise HTTPException(status_code=400, detail="默认形象版本不能删除")

    character, project = await _load_character_and_project(character_id, request, db)
    state = await _sync_project_state(project, [character], db)
    variants = state[character.id]
    if variant_key not in variants:
        raise HTTPException(status_code=404, detail="形象版本不存在")

    entry = variants[variant_key]
    cos_object_key = entry.get("cos_object_key")
    if cos_object_key:
        try:
            await tencent_cos_storage.delete_object(object_key=cos_object_key)
        except Exception:
            logger.warning("删除角色形象 COS 文件失败: character_id=%s variant_key=%s", character.id, variant_key, exc_info=True)

    lock = await _get_state_lock(project.id)
    async with lock:
        _delete_variant_state(project_id=project.id, character_id=character.id, variant_key=variant_key)
    return {"message": "形象版本已删除", "variant_key": variant_key}


@router.post("/projects/{project_id}/initialize", response_model=CharacterImageInitializeResponse, summary="初始化项目角色与组织形象图")
async def initialize_project_character_images(
    project_id: str,
    payload: CharacterImageInitializeRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user_id = getattr(request.state, "user_id", None)
    project = await verify_project_access(project_id, user_id, db)
    result = await db.execute(
        select(Character)
        .where(Character.project_id == project_id)
        .order_by(Character.created_at.asc())
    )
    characters = result.scalars().all()
    ordered_characters = sorted(characters, key=_role_priority)

    if payload.limit is not None:
        ordered_characters = ordered_characters[:payload.limit]

    character_candidates = sum(1 for character in ordered_characters if not character.is_organization)
    organization_candidates = len(ordered_characters) - character_candidates

    generated = 0
    skipped = 0
    failed = 0
    character_processed = 0
    organization_processed = 0
    items: list[CharacterImageActionResponse] = []

    for character in ordered_characters:
        if character.is_organization:
            organization_processed += 1
        else:
            character_processed += 1
        response, executed = await _generate_for_character(
            project=project,
            character=character,
            overwrite=payload.overwrite,
            db=db,
        )
        items.append(response)
        if not executed:
            skipped += 1
        elif response.status == "ready":
            generated += 1
        else:
            failed += 1

    return CharacterImageInitializeResponse(
        project_id=project_id,
        total_candidates=len(ordered_characters),
        character_candidates=character_candidates,
        organization_candidates=organization_candidates,
        generated=generated,
        skipped=skipped,
        failed=failed,
        character_processed=character_processed,
        organization_processed=organization_processed,
        items=items,
    )


@router.get("/characters/{character_id}/image", summary="获取角色形象图文件")
async def get_character_image_file(
    character_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    return await get_character_image_variant_file(
        character_id=character_id,
        variant_key=DEFAULT_VARIANT_KEY,
        request=request,
        db=db,
    )


@router.get("/characters/{character_id}/variants/{variant_key}/image", summary="获取角色指定形象版本图片")
async def get_character_image_variant_file(
    character_id: str,
    variant_key: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    character, project = await _load_character_and_project(character_id, request, db)
    state = await _sync_project_state(project, [character], db)
    variants = state[character.id]
    if variant_key not in variants:
        raise HTTPException(status_code=404, detail="形象版本不存在")
    entry = variants[variant_key]

    cos_url = entry.get("cos_url")
    if isinstance(cos_url, str) and cos_url.strip():
        return RedirectResponse(url=cos_url.strip(), status_code=307, headers=_image_no_cache_headers())

    cos_object_key = entry.get("cos_object_key")
    if cos_object_key:
        try:
            read_url = await tencent_cos_storage.get_read_url(object_key=cos_object_key)
            if read_url.startswith("http://") or read_url.startswith("https://"):
                return RedirectResponse(url=read_url, status_code=307, headers=_image_no_cache_headers())
        except Exception:
            logger.warning("获取角色形象图 COS URL 失败，尝试流式下载: character_id=%s", character.id, exc_info=True)
        try:
            content, content_type = await tencent_cos_storage.download_bytes(object_key=cos_object_key)
            return Response(
                content=content,
                media_type=content_type or entry.get("content_type") or "image/png",
                headers=_image_no_cache_headers(),
            )
        except Exception:
            logger.warning("从 COS 读取角色形象图失败: character_id=%s", character.id, exc_info=True)
    raise HTTPException(status_code=404, detail="角色形象图不存在")


# ── 角色圣经多视角批量生成 ──────────────────────────────────────────────

BIBLE_IMAGE_ROOT = CHARACTER_IMAGE_ROOT  # 同级目录，用 bible/ 子目录区分

BIBLE_VIEW_LABELS = {
    "front_full": "正面全身",
    "front_portrait": "正面半身",
    "three_quarter": "四分之三侧身",
    "side": "纯侧面",
    "back": "背面全身",
}

BIBLE_TASK_STATE: dict[str, dict[str, Any]] = {}


def _bible_image_dir(project_id: str, character_id: str) -> Path:
    return BIBLE_IMAGE_ROOT / project_id / "bible" / character_id


def _normalize_bible_items(items: Any, default: dict[str, str]) -> list[dict[str, str]]:
    if not isinstance(items, list) or not items:
        return [default]

    normalized: list[dict[str, str]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        normalized.append({
            "angle": str(item.get("angle") or ""),
            "name": str(item.get("name") or ""),
            "description": str(item.get("description") or ""),
        })
    return normalized or [default]


def _bible_file_name(view: dict[str, str], expr: dict[str, str], outfit: dict[str, str]) -> str:
    angle = _safe_filename_part(view.get("angle") or "view")
    expr_name = _safe_filename_part(expr.get("name") or "default-expression")
    outfit_name = _safe_filename_part(outfit.get("name") or "default-outfit")
    return f"{angle}__{expr_name}__{outfit_name}.png"

def _parse_legacy_bible_file_name(file_path: Path) -> tuple[str, str, str]:
    parts = file_path.stem.split("_")
    if len(parts) >= 2 and "_".join(parts[:2]) in BIBLE_VIEW_LABELS:
        angle = "_".join(parts[:2])
        expression = parts[2] if len(parts) > 2 else ""
        outfit = "_".join(parts[3:]) if len(parts) > 3 else ""
        return angle, expression, outfit
    angle = parts[0] if parts else ""
    expression = parts[1] if len(parts) > 1 else ""
    outfit = "_".join(parts[2:]) if len(parts) > 2 else ""
    return angle, expression, outfit


def _bible_metadata_for_file(character: Character, file_path: Path) -> tuple[str, str, str]:
    visual_bible = character.visual_bible if isinstance(character.visual_bible, dict) else {}
    views = _normalize_bible_items(visual_bible.get("views"), {"angle": "", "description": ""})
    expressions = _normalize_bible_items(visual_bible.get("expressions"), {"name": "", "description": ""})
    outfits = _normalize_bible_items(visual_bible.get("outfits"), {"name": "", "description": ""})

    for view in views:
        for expr in expressions:
            for outfit in outfits:
                if _bible_file_name(view, expr, outfit) == file_path.name:
                    return view.get("angle", ""), expr.get("name", ""), outfit.get("name", "")

    return _parse_legacy_bible_file_name(file_path)


def _resolve_bible_image_path(project_id: str, character_id: str, file_name: str) -> Path:
    safe_name = Path(file_name).name
    if safe_name != file_name or not safe_name.lower().endswith(".png"):
        raise HTTPException(status_code=400, detail="圣经图片文件名无效")

    bible_dir = _bible_image_dir(project_id, character_id).resolve()
    file_path = (bible_dir / safe_name).resolve()
    if file_path.parent != bible_dir:
        raise HTTPException(status_code=400, detail="圣经图片文件名无效")
    return file_path


def _bible_manifest_entries(character: Character) -> list[dict[str, Any]]:
    visual_bible = character.visual_bible if isinstance(character.visual_bible, dict) else {}
    raw_entries = visual_bible.get(BIBLE_MANIFEST_KEY)
    if not isinstance(raw_entries, list):
        return []
    return [dict(entry) for entry in raw_entries if isinstance(entry, dict)]


def _bible_replace_manifest(character: Character, entries: list[dict[str, Any]]) -> None:
    visual_bible = dict(character.visual_bible or {}) if isinstance(character.visual_bible, dict) else {}
    visual_bible[BIBLE_MANIFEST_KEY] = entries
    character.visual_bible = visual_bible


def _bible_manifest_index(entries: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for entry in entries:
        file_name = str(entry.get("file_name") or "").strip()
        if file_name:
            indexed[file_name] = entry
    return indexed


def _bible_image_url(file_name: str, *, cos_url: str | None = None, cos_object_key: str | None = None) -> str:
    if cos_url and cos_url.strip():
        return cos_url.strip()
    if cos_object_key and tencent_cos_storage.is_enabled():
        return tencent_cos_storage.public_url(cos_object_key)
    raise RuntimeError(f"圣经图片 {file_name} 缺少 COS 访问地址")


def _bible_entry_to_item(entry: dict[str, Any]) -> BibleImageItem:
    return BibleImageItem(
        file_name=str(entry.get("file_name") or ""),
        url=_bible_image_url(
            str(entry.get("file_name") or ""),
            cos_url=entry.get("cos_url"),
            cos_object_key=entry.get("cos_object_key"),
        ),
        angle=str(entry.get("angle") or ""),
        expression=str(entry.get("expression") or ""),
        outfit=str(entry.get("outfit") or ""),
    )


async def _upload_bible_image_to_cos(
    *,
    project: Project,
    character: Character,
    file_name: str,
    image_bytes: bytes,
) -> COSObjectMetadata:
    _require_cos_image_storage()
    object_key = tencent_cos_storage.build_object_key("character-images", project.id, "bible", character.id, file_name)
    return await tencent_cos_storage.upload_bytes(
        object_key=object_key,
        content=image_bytes,
        content_type="image/png",
    )


def _build_bible_prompt(
    trigger_token: str,
    immutable_traits: Any,
    view_desc: str,
    expression_desc: str = "",
    outfit_desc: str = "",
    gender: Optional[str] = None,
    comic_style_instruction: str = "",
) -> str:
    parts = [f"{trigger_token},"]
    trait_parts = []
    if isinstance(immutable_traits, dict):
        trait_values = immutable_traits.values()
    elif isinstance(immutable_traits, list):
        trait_values = immutable_traits
    elif immutable_traits:
        trait_values = [immutable_traits]
    else:
        trait_values = []
    for value in trait_values:
        if value:
            trait_parts.append(str(value))
    if trait_parts:
        parts.append("，".join(trait_parts))
    if outfit_desc:
        parts.append(outfit_desc)
    if view_desc:
        parts.append(view_desc)
    if expression_desc:
        parts.append(expression_desc)
    if comic_style_instruction:
        parts.append(comic_style_instruction)
    prompt = "，".join(parts)
    return _ensure_prompt_suffix(prompt, gender=gender, is_organization=False)


async def _batch_generate_bible_views(
    character_id: str,
    *,
    angles: Optional[list[str]],
    max_concurrent: int,
    user_id: str,
    overwrite: bool,
):
    _require_cos_image_storage()
    engine = await get_engine(user_id)
    db_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with db_factory() as db:
        try:
            character = await db.get(Character, character_id)
            if not character:
                logger.error("角色不存在: character_id=%s", character_id)
                return
            project = await db.get(Project, character.project_id)
            if not project:
                logger.error("项目不存在: project_id=%s", character.project_id)
                return

            visual_bible = character.visual_bible if isinstance(character.visual_bible, dict) else None
            if not visual_bible or not visual_bible.get("trigger_token"):
                logger.error("角色没有视觉圣经: character_id=%s", character_id)
                return

            immutable_traits = visual_bible.get("immutable_traits", {})
            views = [
                item for item in _normalize_bible_items(visual_bible.get("views"), {"angle": "", "description": ""})
                if item.get("angle") or item.get("description")
            ]
            expressions = _normalize_bible_items(visual_bible.get("expressions"), {"name": "", "description": ""})
            outfits = _normalize_bible_items(visual_bible.get("outfits"), {"name": "", "description": ""})

            if angles:
                views = [v for v in views if v.get("angle") in angles]

            if not views:
                logger.error("没有可生成的视角")
                return

            user_settings = await _load_image_settings(user_id, db)
            manifest_entries = _bible_manifest_entries(character)
            manifest_index = _bible_manifest_index(manifest_entries)

            combinations = []
            for view in views:
                for expr in expressions:
                    for outfit in outfits:
                        combinations.append((view, expr, outfit))

            task_key = f"bible:{character_id}"
            BIBLE_TASK_STATE[task_key] = {
                "status": "generating",
                "total": len(combinations),
                "completed": 0,
                "failed": 0,
            }
            logger.info("角色圣经批量生成开始: character_id=%s total=%d", character_id, len(combinations))

            sem = asyncio.Semaphore(max_concurrent)
            generated_entries: list[dict[str, Any]] = []
            generated_lock = asyncio.Lock()

            async def generate_one(view: dict, expr: dict, outfit: dict):
                async with sem:
                    file_name = _bible_file_name(view, expr, outfit)
                    if file_name in manifest_index and not overwrite:
                        BIBLE_TASK_STATE[task_key]["completed"] += 1
                        return

                    prompt = _build_bible_prompt(
                        trigger_token=visual_bible["trigger_token"],
                        immutable_traits=immutable_traits,
                        view_desc=view.get("description", ""),
                        expression_desc=expr.get("description", ""),
                        outfit_desc=outfit.get("description", ""),
                        gender=character.gender,
                        comic_style_instruction=_project_comic_style_instruction(project),
                    )
                    try:
                        prompt_for_generation = append_visible_text_rule(
                            _append_project_comic_style(prompt, project),
                            user_settings.get("image_text_language"),
                        )
                        bible_generation = CharacterImageWorkflowAgent.prepare_bible_generation(
                            project_id=project.id,
                            character_id=character.id,
                            file_name=file_name,
                            prompt_text=prompt_for_generation,
                            provider_profile=user_settings.get("provider_profile", {}),
                        )
                        image_bytes, _ = await _generate_image_with_retry(
                            prompt_for_generation,
                            api_key=user_settings["api_key"],
                            base_url=user_settings["base_url"],
                            model=user_settings["model"],
                            seed=bible_generation["seed"],
                            gender=character.gender,
                            provider_profile=user_settings.get("provider_profile") or {},
                        )
                        cos_metadata = await _upload_bible_image_to_cos(
                            project=project,
                            character=character,
                            file_name=file_name,
                            image_bytes=image_bytes,
                        )
                        entry = {
                            "file_name": file_name,
                            "angle": str(view.get("angle") or ""),
                            "expression": str(expr.get("name") or ""),
                            "outfit": str(outfit.get("name") or ""),
                            "cos_bucket": cos_metadata.bucket,
                            "cos_region": cos_metadata.region,
                            "cos_object_key": cos_metadata.object_key,
                            "cos_url": cos_metadata.url,
                            "cos_etag": cos_metadata.etag,
                            "content_type": cos_metadata.content_type,
                            "content_length": cos_metadata.content_length,
                            "updated_at": _utc_now_iso(),
                        }
                        async with generated_lock:
                            generated_entries.append(entry)
                        BIBLE_TASK_STATE[task_key]["completed"] += 1
                        logger.info("圣经图片生成成功并上传 COS: %s/%s", character_id, file_name)
                    except Exception:
                        BIBLE_TASK_STATE[task_key]["failed"] += 1
                        logger.warning("圣经图片生成失败: %s/%s", character_id, file_name, exc_info=True)

            await asyncio.gather(*(generate_one(v, e, o) for v, e, o in combinations))
            if generated_entries:
                existing_by_file = {entry["file_name"]: entry for entry in manifest_entries}
                for entry in generated_entries:
                    existing_by_file[entry["file_name"]] = entry
                final_entries = sorted(existing_by_file.values(), key=lambda item: item["file_name"])
                visual_bible = dict(character.visual_bible or {}) if isinstance(character.visual_bible, dict) else {}
                visual_bible[BIBLE_MANIFEST_KEY] = final_entries
                character.visual_bible = visual_bible
                await db.commit()
            BIBLE_TASK_STATE[task_key]["status"] = "completed"
            logger.info(
                "角色圣经批量生成完成: character_id=%s completed=%d failed=%d",
                character_id,
                BIBLE_TASK_STATE[task_key]["completed"],
                BIBLE_TASK_STATE[task_key]["failed"],
            )
        except Exception:
            logger.error("角色圣经批量生成异常: character_id=%s", character_id, exc_info=True)
            task_key = f"bible:{character_id}"
            if task_key in BIBLE_TASK_STATE:
                BIBLE_TASK_STATE[task_key]["status"] = "failed"


class BibleGenerateRequest(BaseModel):
    angles: Optional[list[str]] = Field(None, description="要生成的视角列表，为空则全部")
    max_concurrent: int = Field(2, ge=1, le=5, description="最大并发数")
    overwrite: bool = Field(True, description="是否覆盖已存在的圣经图片")


class BibleImageItem(BaseModel):
    file_name: str
    url: str
    angle: str
    expression: str
    outfit: str


class BibleImageListResponse(BaseModel):
    character_id: str
    images: list[BibleImageItem]
    task: Optional[dict[str, Any]] = None


@router.post("/characters/{character_id}/bible/generate", summary="批量生成角色圣经多视角图片")
async def generate_bible_images(
    character_id: str,
    body: BibleGenerateRequest,
    background_tasks: BackgroundTasks,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    _require_cos_image_storage()
    character, project = await _load_character_and_project(character_id, request, db)
    visual_bible = character.visual_bible if isinstance(character.visual_bible, dict) else None
    if not visual_bible or not visual_bible.get("trigger_token"):
        raise HTTPException(status_code=400, detail="该角色没有视觉圣经数据，请先生成角色")

    task_key = f"bible:{character_id}"
    if task_key in BIBLE_TASK_STATE and BIBLE_TASK_STATE[task_key].get("status") == "generating":
        raise HTTPException(status_code=409, detail="该角色正在生成圣经图片，请等待完成")

    views = [
        item for item in _normalize_bible_items(visual_bible.get("views"), {"angle": "", "description": ""})
        if item.get("angle") or item.get("description")
    ]
    if body.angles:
        views = [v for v in views if v.get("angle") in body.angles]
    if not views:
        raise HTTPException(status_code=400, detail="没有可生成的圣经视角")
    expressions = _normalize_bible_items(visual_bible.get("expressions"), {"name": "", "description": ""})
    outfits = _normalize_bible_items(visual_bible.get("outfits"), {"name": "", "description": ""})
    total = len(views) * max(1, len(expressions)) * max(1, len(outfits))

    user_id = getattr(request.state, "user_id", None) or project.user_id
    background_tasks.add_task(
        _batch_generate_bible_views,
        character_id,
        angles=body.angles,
        max_concurrent=body.max_concurrent,
        user_id=user_id,
        overwrite=body.overwrite,
    )
    logger.info("角色圣经批量生成已启动: character_id=%s total=%d", character_id, total)
    return {"status": "started", "total": total}


@router.get("/characters/{character_id}/bible/images", summary="获取角色圣经已生成图片列表")
async def get_bible_images(
    character_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> BibleImageListResponse:
    character, project = await _load_character_and_project(character_id, request, db)
    images: list[BibleImageItem] = []
    for entry in _bible_manifest_entries(character):
        try:
            images.append(_bible_entry_to_item(entry))
        except RuntimeError:
            continue

    task_key = f"bible:{character_id}"
    task_state = BIBLE_TASK_STATE.get(task_key)

    return BibleImageListResponse(
        character_id=character_id,
        images=images,
        task=task_state,
    )


@router.get("/characters/{character_id}/bible/images/{file_name}", summary="获取角色圣经单张图片")
async def get_bible_image_file(
    character_id: str,
    file_name: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    character, project = await _load_character_and_project(character_id, request, db)
    manifest_index = _bible_manifest_index(_bible_manifest_entries(character))
    entry = manifest_index.get(Path(file_name).name)
    if not entry:
        raise HTTPException(status_code=404, detail="圣经图片不存在")
    cos_url = entry.get("cos_url")
    if isinstance(cos_url, str) and cos_url.strip():
        return RedirectResponse(url=cos_url.strip(), status_code=307, headers=_image_no_cache_headers())
    cos_object_key = entry.get("cos_object_key")
    if cos_object_key:
        try:
            read_url = await tencent_cos_storage.get_read_url(object_key=cos_object_key)
            if read_url.startswith("http://") or read_url.startswith("https://"):
                return RedirectResponse(url=read_url, status_code=307, headers=_image_no_cache_headers())
        except Exception:
            logger.warning("获取角色圣经图片 COS URL 失败，尝试流式下载: character_id=%s file_name=%s", character_id, file_name, exc_info=True)
        try:
            content, content_type = await tencent_cos_storage.download_bytes(object_key=cos_object_key)
            return Response(
                content=content,
                media_type=content_type or "image/png",
                headers=_image_no_cache_headers(),
            )
        except Exception:
            logger.warning("从 COS 读取角色圣经图片失败: character_id=%s file_name=%s", character_id, file_name, exc_info=True)
    raise HTTPException(status_code=404, detail="圣经图片不存在")


@router.delete("/characters/{character_id}/bible/images/{file_name}", summary="删除角色圣经单张图片")
async def delete_bible_image(
    character_id: str,
    file_name: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    character, project = await _load_character_and_project(character_id, request, db)
    manifest_entries = _bible_manifest_entries(character)
    manifest_index = _bible_manifest_index(manifest_entries)
    entry = manifest_index.get(Path(file_name).name)
    if not entry:
        raise HTTPException(status_code=404, detail="圣经图片不存在")
    cos_object_key = entry.get("cos_object_key")
    if cos_object_key:
        try:
            await tencent_cos_storage.delete_object(object_key=cos_object_key)
        except Exception:
            logger.warning("删除角色圣经 COS 文件失败: character_id=%s file_name=%s", character_id, file_name, exc_info=True)
    manifest_entries = [item for item in manifest_entries if str(item.get("file_name") or "") != Path(file_name).name]
    _bible_replace_manifest(character, manifest_entries)
    await db.commit()
    return {"status": "deleted", "file_name": file_name}


class BibleImageEditRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=2000, description="改图提示词")


@router.post("/characters/{character_id}/bible/images/{file_name}/regenerate", summary="改图：基于原图修改圣经图片")
async def regenerate_bible_image(
    character_id: str,
    file_name: str,
    body: BibleImageEditRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """基于现有圣经图片 + 用户提示词，调用 img2img 改图 API 生成新图并覆盖原 COS 对象"""
    _require_cos_image_storage()
    character, project = await _load_character_and_project(character_id, request, db)
    manifest_entries = _bible_manifest_entries(character)
    manifest_index = _bible_manifest_index(manifest_entries)
    entry = manifest_index.get(Path(file_name).name)
    if not entry:
        raise HTTPException(status_code=404, detail="圣经图片不存在")
    cos_object_key = entry.get("cos_object_key")
    if not cos_object_key:
        raise HTTPException(status_code=404, detail="圣经图片不存在")

    user_id = getattr(request.state, "user_id", None)
    settings = await _load_image_settings(user_id, db)
    provider_profile = settings.get("provider_profile") or {}
    model = resolve_image_edit_model(settings["model"], provider_profile=provider_profile)

    image_bytes, _ = await tencent_cos_storage.download_bytes(object_key=cos_object_key)
    new_bytes, _ = await _edit_image_with_retry(
        _append_project_comic_style(body.prompt, project),
        image_bytes,
        api_key=settings["api_key"],
        base_url=settings["base_url"],
        model=model,
        provider_profile=provider_profile,
    )
    cos_metadata = await _upload_bible_image_to_cos(
        project=project,
        character=character,
        file_name=Path(file_name).name,
        image_bytes=new_bytes,
    )
    updated_entry = dict(entry)
    updated_entry.update(
        {
            "cos_bucket": cos_metadata.bucket,
            "cos_region": cos_metadata.region,
            "cos_object_key": cos_metadata.object_key,
            "cos_url": cos_metadata.url,
            "cos_etag": cos_metadata.etag,
            "content_type": cos_metadata.content_type,
            "content_length": cos_metadata.content_length,
            "updated_at": _utc_now_iso(),
        }
    )
    manifest_entries = [updated_entry if str(item.get("file_name") or "") == Path(file_name).name else item for item in manifest_entries]
    _bible_replace_manifest(character, manifest_entries)
    await db.commit()

    return BibleImageItem(
        file_name=Path(file_name).name,
        url=_bible_image_url(
            Path(file_name).name,
            cos_url=cos_metadata.url if cos_metadata else cos_url,
            cos_object_key=cos_metadata.object_key,
        ),
        angle=str(updated_entry.get("angle") or ""),
        expression=str(updated_entry.get("expression") or ""),
        outfit=str(updated_entry.get("outfit") or ""),
    )
