"""角色图 workflow agent。"""
from __future__ import annotations

from typing import Any

from app.services.image_request_utils import derive_consistency_seed


class CharacterImageWorkflowAgent:
    @staticmethod
    def prepare_variant_generation(
        *,
        project_id: str,
        character_id: str,
        variant_key: str,
        prompt_text: str,
        provider_profile: dict[str, Any] | None,
    ) -> dict[str, Any]:
        profile = provider_profile or {}
        seed = None
        if profile.get("supports_seed"):
            seed = derive_consistency_seed(project_id, character_id, variant_key, prompt_text)
        return {
            "seed": seed,
            "provider_profile": profile,
            "consistency_key": f"{project_id}:{character_id}:{variant_key}",
        }

    @staticmethod
    def prepare_bible_generation(
        *,
        project_id: str,
        character_id: str,
        file_name: str,
        prompt_text: str,
        provider_profile: dict[str, Any] | None,
    ) -> dict[str, Any]:
        profile = provider_profile or {}
        seed = None
        if profile.get("supports_seed"):
            seed = derive_consistency_seed(project_id, character_id, file_name, prompt_text)
        return {
            "seed": seed,
            "provider_profile": profile,
            "consistency_key": f"{project_id}:{character_id}:{file_name}",
        }
