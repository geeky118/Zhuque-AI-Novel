"""分镜 workflow agent。"""
from __future__ import annotations

from typing import Any

from app.services.storyboard_prompt_cache import (
    extract_storyboard_page_character_names,
    is_storyboard_prompt_fresh,
    storyboard_prompt_context_hash,
    storyboard_prompt_request_summary,
)


class StoryboardWorkflowAgent:
    @staticmethod
    def _selected_page_names(page_data: dict[str, Any] | None) -> list[str]:
        if not isinstance(page_data, dict):
            return []
        return extract_storyboard_page_character_names(page_data)

    @classmethod
    def build_character_reference_brief(
        cls,
        references: list[dict[str, Any]] | None,
        *,
        page_data: dict[str, Any] | None = None,
        page_character_names: list[str] | None = None,
    ) -> str:
        if not references:
            return ""

        selected_names = [name for name in (page_character_names or cls._selected_page_names(page_data)) if name]
        selected = [
            ref for ref in references
            if isinstance(ref, dict) and (
                not selected_names or str(ref.get("name") or "").strip() in selected_names
            )
        ]
        if not selected:
            if selected_names:
                return ""
            selected = [ref for ref in references if isinstance(ref, dict)]

        lines: list[str] = []
        for ref in selected[:8]:
            name = str(ref.get("name") or "").strip()
            if not name:
                continue
            variant_label = str(ref.get("variant_label") or "").strip()
            variant_type = str(ref.get("variant_type") or "").strip()
            prompt = str(ref.get("prompt") or "").strip()
            image_url = str(ref.get("image_url") or "").strip()
            parts = [name]
            if variant_label:
                parts.append(f"形象版本：{variant_label}")
            if variant_type:
                parts.append(f"类型：{variant_type}")
            if prompt:
                parts.append(f"视觉锚点：{prompt[:260]}")
            if image_url.startswith("http"):
                parts.append(f"参考图：{image_url}")
            lines.append("- " + " | ".join(parts))
        return "\n".join(lines)

    @classmethod
    def build_reference_images(
        cls,
        references: list[dict[str, Any]] | None,
        *,
        page_data: dict[str, Any] | None = None,
        page_character_names: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        if not references:
            return []

        selected_names = [name for name in (page_character_names or cls._selected_page_names(page_data)) if name]
        payload: list[dict[str, Any]] = []
        for ref in references:
            if not isinstance(ref, dict):
                continue
            image_url = str(ref.get("image_url") or "").strip()
            if not image_url.startswith("http"):
                continue
            name = str(ref.get("name") or "").strip()
            if selected_names and name not in selected_names:
                continue
            payload.append(
                {
                    "name": name,
                    "variant_key": str(ref.get("variant_key") or "").strip(),
                    "variant_label": str(ref.get("variant_label") or "").strip(),
                    "variant_type": str(ref.get("variant_type") or "").strip(),
                    "image_url": image_url,
                }
            )

        return payload

    @staticmethod
    def context_hash(
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
    ) -> str:
        return storyboard_prompt_context_hash(
            project_id=project_id,
            chapter_number=chapter_number,
            page_number=page_number,
            total_pages=total_pages,
            page_data=page_data,
            continuity_pack=continuity_pack,
            page_context=page_context,
            character_reference_brief=character_reference_brief,
            comic_style_instruction=comic_style_instruction,
        )

    @staticmethod
    def request_summary(
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
        return storyboard_prompt_request_summary(
            project_title=project_title,
            chapter_number=chapter_number,
            chapter_title=chapter_title,
            page_number=page_number,
            page_data=page_data,
            page_context=page_context,
            character_reference_brief=character_reference_brief,
            comic_style_instruction=comic_style_instruction,
        )

    @staticmethod
    def is_prompt_fresh(existing_metadata: dict[str, Any] | None, *, context_hash: str, prompt_version: int) -> bool:
        return is_storyboard_prompt_fresh(existing_metadata, context_hash=context_hash, prompt_version=prompt_version)
