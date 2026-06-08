"""漫画页 workflow agent。"""
from __future__ import annotations

from typing import Any

from app.services.agents.storyboard_agent import StoryboardWorkflowAgent
from app.services.image_request_utils import derive_consistency_seed


class ComicWorkflowAgent:
    @staticmethod
    def prepare_page_generation(
        *,
        project_id: str,
        chapter_number: int,
        page_number: int,
        original_prompt_text: str,
        regen_task: dict[str, Any],
        provider_profile: dict[str, Any] | None,
    ) -> dict[str, Any]:
        profile = provider_profile or {}
        request_summary = regen_task.get("prompt_request_summary") if isinstance(regen_task, dict) else None
        page_character_names = []
        if isinstance(request_summary, dict):
            page_character_names = [
                str(name).strip()
                for name in (request_summary.get("page_character_names") or [])
                if str(name).strip()
            ]

        seed = None
        if profile.get("supports_seed"):
            seed = derive_consistency_seed(
                project_id,
                str(chapter_number),
                str(page_number),
                str(regen_task.get("prompt_context_hash") or ""),
                original_prompt_text,
            )

        reference_images = []
        if profile.get("supports_reference_images"):
            reference_images = StoryboardWorkflowAgent.build_reference_images(
                regen_task.get("character_image_references") or [],
                page_character_names=page_character_names,
            )

        return {
            "seed": seed,
            "reference_images": reference_images,
            "reference_image_count": len(reference_images),
            "page_character_names": page_character_names,
            "provider_profile": profile,
        }
