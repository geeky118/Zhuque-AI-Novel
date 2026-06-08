"""图片生成请求与响应的通用工具。"""
from __future__ import annotations

import base64
import hashlib
from io import BytesIO
from dataclasses import dataclass, asdict
from typing import Any

IMAGE_TEXT_LANGUAGE_DEFAULT = "zh"


@dataclass(frozen=True)
class ImageProviderProfile:
    provider: str
    provider_family: str
    base_url: str
    model: str
    supports_edit: bool
    supports_reference_images: bool
    supports_seed: bool
    supports_extra_params: bool
    capability_level: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalize_image_api_base_urls(base_url: str | None) -> list[str]:
    configured_base_url = (base_url or "").rstrip("/")
    if not configured_base_url:
        return []

    candidates = [configured_base_url]
    if "/sub2api-image/v1" in configured_base_url:
        candidates.append(configured_base_url.replace("/sub2api-image/v1", "/sub2api/v1"))
    elif "/sub2api/v1" in configured_base_url:
        candidates.append(configured_base_url.replace("/sub2api/v1", "/sub2api-image/v1"))
    return list(dict.fromkeys(candidates))


def resolve_image_api_base_url(*, provider: str | None = None, base_url: str | None = None, model: str | None = None) -> str:
    configured_base_url = (base_url or "").strip().rstrip("/")
    return configured_base_url


def derive_consistency_seed(*parts: str) -> int:
    raw = "\u0001".join(part or "" for part in parts)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return int(digest[:8], 16)


def is_openai_image_provider(
    *,
    provider: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
) -> bool:
    normalized_provider = (provider or "").strip().lower()
    normalized_model = (model or "").strip().lower()
    normalized_base_url = (base_url or "").strip().rstrip("/").lower()
    return (
        normalized_provider == "openai"
        or "api.openai.com" in normalized_base_url
        or normalized_model.startswith("gpt-image")
    )


def _coerce_provider_profile(profile: ImageProviderProfile | dict[str, Any] | None) -> dict[str, Any]:
    if isinstance(profile, ImageProviderProfile):
        return profile.as_dict()
    if isinstance(profile, dict):
        return profile
    return {}


def image_profile_is_openai(profile: ImageProviderProfile | dict[str, Any] | None) -> bool:
    profile_data = _coerce_provider_profile(profile)
    return (
        profile_data.get("provider_family") == "openai"
        or profile_data.get("provider") == "openai"
        or is_openai_image_provider(
            provider=str(profile_data.get("provider") or ""),
            base_url=str(profile_data.get("base_url") or ""),
            model=str(profile_data.get("model") or ""),
        )
    )


def image_model_uses_openai_gpt_payload(
    *,
    model: str | None = None,
    provider_profile: ImageProviderProfile | dict[str, Any] | None = None,
) -> bool:
    normalized_model = (model or "").strip().lower()
    return normalized_model.startswith("gpt-image") or image_profile_is_openai(provider_profile)


def resolve_image_edit_model(
    model: str,
    *,
    provider_profile: ImageProviderProfile | dict[str, Any] | None = None,
) -> str:
    normalized_model = (model or "").strip()
    if image_model_uses_openai_gpt_payload(model=normalized_model, provider_profile=provider_profile):
        return normalized_model
    return normalized_model if normalized_model.endswith("-edit") else f"{normalized_model}-edit"


def normalize_image_text_language(value: str | None) -> str:
    normalized = (value or "").strip().lower()
    if normalized in {"zh", "zh-cn", "cn", "chinese", "中文", "简体中文"}:
        return "zh"
    if normalized in {"en", "english", "英文"}:
        return "en"
    return IMAGE_TEXT_LANGUAGE_DEFAULT


def image_text_language_label(value: str | None) -> str:
    return "英文" if normalize_image_text_language(value) == "en" else "中文"


def build_visible_text_rule(language: str | None, *, scope: str = "image") -> str:
    if normalize_image_text_language(language) == "en":
        return (
            "Visible text rule: any visible text inside the image, including speech bubbles, captions, signs and sound effects, "
            "must be English only. Do not render Chinese characters. If source dialogue is Chinese, translate or summarize it into short natural English before drawing it. "
            "If translation is uncertain, omit the text bubble instead of drawing Chinese."
        )
    if scope == "comic":
        return (
            "画面文字规则：图像中的对话气泡、旁白、标牌和拟声词等可见文字必须使用简体中文，字数要短、清晰、自然。"
            "原文对白是中文时，保留或压缩为简体中文；原文对白不是中文时，译写为简短自然的简体中文。避免乱码、随机字符和无意义文字。"
        )
    return "画面内可见文字规则：图像中的文字必须使用简体中文，文字要短、清晰、自然，避免乱码和无意义字符。"


def append_visible_text_rule(prompt: str, language: str | None, *, scope: str = "image") -> str:
    rule = build_visible_text_rule(language, scope=scope)
    if rule in prompt:
        return prompt.strip()
    return f"{prompt.strip()}\n\n{rule}".strip()


def _contains_cjk_text(value: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in value or "")


def build_dialogue_reference(dialogue: str, language: str | None) -> str:
    if normalize_image_text_language(language) == "en":
        if _contains_cjk_text(dialogue):
            return "Dialogue reference: use a short natural English speech bubble that conveys the emotion; do not render the source-language text."
        return f"Dialogue reference: {dialogue}"
    if _contains_cjk_text(dialogue):
        return f"对白参考：{dialogue}。如果需要在画面中出现气泡文字，请保留或压缩为简短自然的简体中文。"
    return f"对白参考：{dialogue}。如果需要在画面中出现气泡文字，请译写为简短自然的简体中文。"


def resolve_image_provider_profile(
    *,
    provider: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
) -> ImageProviderProfile:
    normalized_provider = (provider or "").strip().lower()
    normalized_model = (model or "").strip().lower()
    normalized_base_url = (base_url or "").strip().rstrip("/")
    provider_family = "openai_compatible" if normalized_base_url or normalized_provider else "unknown"

    supports_edit = True
    supports_extra_params = True

    supports_reference_images = False
    supports_seed = False
    capability_level = "basic"

    if is_openai_image_provider(provider=normalized_provider, base_url=normalized_base_url, model=normalized_model):
        normalized_provider = normalized_provider or "openai"
        provider_family = "openai"
        supports_reference_images = True
        capability_level = "openai"
    elif normalized_provider in {"mumu", "hermes"}:
        capability_level = "extended"
        if any(token in normalized_model for token in ("reference", "consistency", "seed", "flux", "controlnet")):
            supports_reference_images = True
            supports_seed = True
            capability_level = "advanced"
    elif normalized_provider in {"grok", "gemini"}:
        capability_level = "basic"
    elif normalized_provider:
        capability_level = "basic"
        if any(token in normalized_model for token in ("reference", "seed")):
            supports_reference_images = True
            supports_seed = True
            capability_level = "extended"

    return ImageProviderProfile(
        provider=normalized_provider or "unknown",
        provider_family=provider_family,
        base_url=normalized_base_url,
        model=normalized_model,
        supports_edit=supports_edit,
        supports_reference_images=supports_reference_images,
        supports_seed=supports_seed,
        supports_extra_params=supports_extra_params,
        capability_level=capability_level,
    )


def build_image_generation_payload(
    prompt: str,
    *,
    model: str,
    size: str,
    response_format: str = "b64_json",
    n: int = 1,
    reference_images: list[dict[str, Any]] | None = None,
    seed: int | None = None,
    extra_params: dict[str, Any] | None = None,
    provider_profile: ImageProviderProfile | dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "n": n,
        "size": size,
    }
    if not image_model_uses_openai_gpt_payload(model=model, provider_profile=provider_profile):
        payload["response_format"] = response_format
    if reference_images:
        if image_profile_is_openai(provider_profile):
            payload["images"] = normalize_openai_reference_images(reference_images)
        else:
            payload["reference_images"] = reference_images
    if seed is not None and not image_profile_is_openai(provider_profile):
        payload["seed"] = seed
    if extra_params:
        payload.update(extra_params)
    return payload


def build_image_edit_payload(
    prompt: str,
    *,
    model: str,
    size: str,
    response_format: str = "b64_json",
    extra_params: dict[str, Any] | None = None,
    provider_profile: ImageProviderProfile | dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "prompt": prompt,
        "model": model,
        "size": size,
    }
    if not image_model_uses_openai_gpt_payload(model=model, provider_profile=provider_profile):
        payload["response_format"] = response_format
    if extra_params:
        payload.update(extra_params)
    return payload


def normalize_openai_reference_images(reference_images: list[dict[str, Any]]) -> list[Any]:
    images: list[Any] = []
    for reference in reference_images:
        if not isinstance(reference, dict):
            continue
        image_ref = reference.get("file_id") or reference.get("image_url") or reference.get("url")
        if isinstance(image_ref, str) and image_ref.strip():
            images.append(image_ref.strip())
    return images


def decode_b64_image_response(data: Any) -> tuple[bytes, str | None]:
    if not isinstance(data, dict):
        raise ValueError("图片接口返回格式不正确")

    images = data.get("data")
    if not isinstance(images, list) or not images:
        raise ValueError("图片接口未返回图片数据")

    first_item = images[0]
    if not isinstance(first_item, dict):
        raise ValueError("图片接口图片数据格式不正确")

    image_b64 = first_item.get("b64_json")
    if not isinstance(image_b64, str) or not image_b64.strip():
        raise ValueError("图片接口未返回 b64_json")

    try:
        image_bytes = base64.b64decode(image_b64)
    except (ValueError, TypeError) as exc:
        raise ValueError("图片内容解码失败") from exc

    revised_prompt = first_item.get("revised_prompt")
    return image_bytes, revised_prompt if isinstance(revised_prompt, str) else None


def normalize_image_bytes_to_png(
    image_bytes: bytes,
) -> tuple[bytes, str | None, str | None]:
    try:
        from PIL import Image, UnidentifiedImageError
    except ImportError as exc:  # pragma: no cover - depends on environment packaging
        raise RuntimeError("图片格式归一化需要 Pillow 依赖") from exc

    if not image_bytes:
        return b"", None, "empty_image_bytes"

    try:
        with Image.open(BytesIO(bytes(image_bytes))) as image:
            image.load()
            source_format = (image.format or "").lower() or None
            if source_format == "png":
                return bytes(image_bytes), source_format, None

            has_alpha = "A" in image.getbands() or image.info.get("transparency") is not None
            target_mode = "RGBA" if has_alpha else "RGB"
            normalized_image = image.convert(target_mode) if image.mode != target_mode else image

            output = BytesIO()
            normalized_image.save(output, format="PNG")
            return output.getvalue(), source_format, None
    except UnidentifiedImageError:
        return b"", None, "invalid_image_format"
    except Exception as exc:
        return b"", None, f"image_decode_failed:{exc}"
