from __future__ import annotations

import base64
from io import BytesIO

from PIL import Image

from app.services.image_request_utils import (
    build_image_generation_payload,
    build_image_edit_payload,
    decode_b64_image_response,
    derive_consistency_seed,
    image_profile_is_openai,
    normalize_image_bytes_to_png,
    resolve_image_api_base_url,
    resolve_image_edit_model,
    resolve_image_provider_profile,
)


def test_resolve_image_provider_profile_marks_seed_and_reference_support() -> None:
    profile = resolve_image_provider_profile(provider="hermes", base_url="https://example.com/v1", model="flux-reference-seed")

    assert profile.provider == "hermes"
    assert profile.provider_family == "openai_compatible"
    assert profile.supports_reference_images is True
    assert profile.supports_seed is True
    assert profile.capability_level == "advanced"


def test_derive_consistency_seed_is_stable_and_order_sensitive() -> None:
    seed_a = derive_consistency_seed("project-1", "chapter-2", "page-3", "prompt")
    seed_b = derive_consistency_seed("project-1", "chapter-2", "page-3", "prompt")
    seed_c = derive_consistency_seed("project-1", "page-3", "chapter-2", "prompt")

    assert seed_a == seed_b
    assert seed_a != seed_c


def test_build_image_generation_payload_keeps_seed_and_reference_images() -> None:
    payload = build_image_generation_payload(
        "prompt",
        model="model-a",
        size="720x1280",
        seed=1234,
        reference_images=[{"image_url": "https://example.com/ref.png"}],
        extra_params={"style": "comic"},
    )

    assert payload["seed"] == 1234
    assert payload["reference_images"] == [{"image_url": "https://example.com/ref.png"}]
    assert payload["style"] == "comic"


def test_resolve_openai_image_provider_profile_uses_explicit_base_url() -> None:
    profile = resolve_image_provider_profile(provider="openai", model="gpt-image-1")

    assert profile.provider == "openai"
    assert profile.provider_family == "openai"
    assert profile.supports_edit is True
    assert profile.supports_reference_images is True
    assert profile.supports_seed is False
    assert image_profile_is_openai(profile)
    assert resolve_image_api_base_url(provider="openai", model="gpt-image-1") == ""
    assert (
        resolve_image_api_base_url(
            provider="openai",
            base_url="https://example.com/openai/v1",
            model="gpt-image-1",
        )
        == "https://example.com/openai/v1"
    )


def test_openai_gpt_image_generation_payload_omits_unsupported_compatible_fields() -> None:
    profile = resolve_image_provider_profile(provider="openai", model="gpt-image-1")

    payload = build_image_generation_payload(
        "prompt",
        model="gpt-image-1",
        size="1024x1024",
        seed=1234,
        reference_images=[
            {"image_url": "https://example.com/ref.png"},
            {"file_id": "file-123"},
        ],
        provider_profile=profile,
    )

    assert payload["model"] == "gpt-image-1"
    assert payload["images"] == ["https://example.com/ref.png", "file-123"]
    assert "reference_images" not in payload
    assert "seed" not in payload
    assert "response_format" not in payload


def test_openai_gpt_image_edit_payload_and_model_do_not_append_edit_suffix() -> None:
    profile = resolve_image_provider_profile(provider="openai", model="gpt-image-1")
    edit_model = resolve_image_edit_model("gpt-image-1", provider_profile=profile)

    payload = build_image_edit_payload(
        "prompt",
        model=edit_model,
        size="1024x1024",
        provider_profile=profile,
    )

    assert edit_model == "gpt-image-1"
    assert payload == {
        "prompt": "prompt",
        "model": "gpt-image-1",
        "size": "1024x1024",
    }


def test_compatible_image_edit_model_still_appends_edit_suffix() -> None:
    profile = resolve_image_provider_profile(provider="hermes", model="flux-reference-seed")

    assert resolve_image_edit_model("flux-reference-seed", provider_profile=profile) == "flux-reference-seed-edit"


def test_decode_b64_image_response_returns_image_bytes_and_revised_prompt() -> None:
    image_bytes = b"png-bytes"
    encoded = base64.b64encode(image_bytes).decode("ascii")

    decoded_bytes, revised_prompt = decode_b64_image_response(
        {
            "data": [
                {
                    "b64_json": encoded,
                    "revised_prompt": "revised prompt",
                }
            ]
        }
    )

    assert decoded_bytes == image_bytes
    assert revised_prompt == "revised prompt"


def _make_image_bytes(*, image_format: str) -> bytes:
    image = Image.new("RGB", (720, 1280), color=(12, 34, 56))
    buffer = BytesIO()
    image.save(buffer, format=image_format)
    return buffer.getvalue()


def test_normalize_image_bytes_to_png_keeps_png_input() -> None:
    png_bytes = _make_image_bytes(image_format="PNG")

    normalized_bytes, source_format, issue = normalize_image_bytes_to_png(
        png_bytes,
    )

    assert issue is None
    assert source_format == "png"
    assert normalized_bytes.startswith(b"\x89PNG\r\n\x1a\n")


def test_normalize_image_bytes_to_png_converts_jpeg_input() -> None:
    jpeg_bytes = _make_image_bytes(image_format="JPEG")

    normalized_bytes, source_format, issue = normalize_image_bytes_to_png(
        jpeg_bytes,
    )

    assert issue is None
    assert source_format == "jpeg"
    assert normalized_bytes.startswith(b"\x89PNG\r\n\x1a\n")
    with Image.open(BytesIO(normalized_bytes)) as image:
        assert image.size == (720, 1280)
        assert image.format == "PNG"


def test_normalize_image_bytes_to_png_rejects_invalid_bytes() -> None:
    normalized_bytes, source_format, issue = normalize_image_bytes_to_png(
        b"not-an-image",
    )

    assert normalized_bytes == b""
    assert source_format is None
    assert issue == "invalid_image_format"


def test_normalize_image_bytes_to_png_accepts_non_standard_size() -> None:
    image = Image.new("RGB", (512, 768), color=(12, 34, 56))
    buffer = BytesIO()
    image.save(buffer, format="JPEG")

    normalized_bytes, source_format, issue = normalize_image_bytes_to_png(buffer.getvalue())

    assert issue is None
    assert source_format == "jpeg"
    with Image.open(BytesIO(normalized_bytes)) as normalized:
        assert normalized.size == (512, 768)
        assert normalized.format == "PNG"
