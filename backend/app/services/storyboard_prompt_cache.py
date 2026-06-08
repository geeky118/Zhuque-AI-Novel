"""分镜提示词 sidecar 缓存与刷新判定。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def _normalize_text(value: Any, *, limit: int | None = None) -> str:
    text = " ".join(str(value or "").split()).strip()
    if limit is not None and len(text) > limit:
        return text[:limit].rstrip() + "…"
    return text


def _extract_string_list(value: Any, *, limit: int | None = None) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        text = _normalize_text(item, limit=limit)
        if text:
            result.append(text)
    return result


def extract_storyboard_page_character_names(page_data: dict[str, Any]) -> list[str]:
    names: list[str] = []
    panels = page_data.get("panels") if isinstance(page_data, dict) else None
    if not isinstance(panels, list):
        panels = page_data.get("panel_plan") if isinstance(page_data, dict) else None
    if not isinstance(panels, list):
        return names

    for panel in panels:
        if not isinstance(panel, dict):
            continue
        characters = panel.get("characters")
        if not isinstance(characters, list):
            continue
        for character in characters:
            text = _normalize_text(character, limit=60)
            if text and text not in names:
                names.append(text)
    return names


def storyboard_prompt_metadata_path(prompt_path: Path) -> Path:
    return prompt_path.with_name(f"{prompt_path.stem}.meta.json")


def storyboard_prompt_context_hash(
    *,
    project_id: str,
    chapter_number: int,
    page_number: int,
    total_pages: int | None,
    page_data: dict[str, Any],
    continuity_pack: str | None,
    page_context: str | None,
    character_reference_brief: str | None,
    comic_style_instruction: str | None,
    image_text_language: str | None = None,
) -> str:
    payload = {
        "version": 2,
        "project_id": project_id,
        "chapter_number": chapter_number,
        "page_number": page_number,
        "total_pages": total_pages,
        "page_goal": _normalize_text(page_data.get("page_goal"), limit=180),
        "scene": _normalize_text(page_data.get("scene"), limit=180),
        "turning_point": _normalize_text(page_data.get("turning_point"), limit=180),
        "panel_count": page_data.get("panel_count"),
        "must_keep": _extract_string_list(page_data.get("must_keep"), limit=120),
        "panel_plan": [
            {
                "panel_number": panel.get("panel_number"),
                "beat": _normalize_text(panel.get("beat"), limit=180),
                "visual_goal": _normalize_text(panel.get("visual_goal"), limit=220),
                "camera_intent": _normalize_text(panel.get("camera_intent"), limit=180),
                "emotion": _normalize_text(panel.get("emotion"), limit=120),
                "dialogue_focus": _normalize_text(panel.get("dialogue_focus"), limit=180),
            }
            for panel in (page_data.get("panel_plan") if isinstance(page_data.get("panel_plan"), list) else [])
            if isinstance(panel, dict)
        ],
        "page_character_names": extract_storyboard_page_character_names(page_data),
        "continuity_pack": continuity_pack or "",
        "page_context": page_context or "",
        "character_reference_brief": character_reference_brief or "",
        "comic_style_instruction": comic_style_instruction or "",
        "image_text_language": image_text_language or "",
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def load_storyboard_prompt_metadata(prompt_path: Path) -> dict[str, Any] | None:
    metadata_path = storyboard_prompt_metadata_path(prompt_path)
    if not metadata_path.is_file():
        return None
    try:
        data = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def write_storyboard_prompt_metadata(prompt_path: Path, metadata: dict[str, Any]) -> None:
    metadata_path = storyboard_prompt_metadata_path(prompt_path)
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def storyboard_prompt_request_summary(
    *,
    project_title: str | None,
    chapter_number: int,
    chapter_title: str | None,
    page_number: int,
    page_data: dict[str, Any],
    page_context: str | None,
    character_reference_brief: str | None,
    comic_style_instruction: str | None,
) -> dict[str, Any]:
    return {
        "project_title": _normalize_text(project_title, limit=120),
        "chapter_number": chapter_number,
        "chapter_title": _normalize_text(chapter_title, limit=120),
        "page_number": page_number,
        "page_goal": _normalize_text(page_data.get("page_goal"), limit=180),
        "scene": _normalize_text(page_data.get("scene"), limit=180),
        "turning_point": _normalize_text(page_data.get("turning_point"), limit=180),
        "page_context": page_context or "",
        "page_character_names": extract_storyboard_page_character_names(page_data),
        "character_reference_brief": character_reference_brief or "",
        "comic_style_instruction": comic_style_instruction or "",
        "panel_count": page_data.get("panel_count"),
        "panel_numbers": [
            panel.get("panel_number")
            for panel in (page_data.get("panel_plan") if isinstance(page_data.get("panel_plan"), list) else [])
            if isinstance(panel, dict)
        ],
    }


def is_storyboard_prompt_fresh(
    existing_metadata: dict[str, Any] | None,
    *,
    context_hash: str,
    prompt_version: int,
) -> bool:
    return bool(
        existing_metadata
        and existing_metadata.get("context_hash") == context_hash
        and existing_metadata.get("prompt_version") == prompt_version
    )
