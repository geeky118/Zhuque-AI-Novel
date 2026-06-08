from __future__ import annotations

from app.services.agents import CharacterImageWorkflowAgent, ComicWorkflowAgent, StoryboardWorkflowAgent


def test_storyboard_agent_filters_reference_images_by_page_character_and_absolute_url() -> None:
    references = [
        {"name": "Alice", "variant_key": "default", "image_url": "https://cdn.example.com/alice.png"},
        {"name": "Alice", "variant_key": "local", "image_url": "/api/character-images/characters/alice/image"},
        {"name": "Bob", "variant_key": "default", "image_url": "https://cdn.example.com/bob.png"},
    ]
    page_data = {
        "panels": [
            {"characters": ["Alice"]},
        ]
    }

    payload = StoryboardWorkflowAgent.build_reference_images(references, page_data=page_data)
    brief = StoryboardWorkflowAgent.build_character_reference_brief(references, page_data=page_data)

    assert payload == [
        {
            "name": "Alice",
            "variant_key": "default",
            "variant_label": "",
            "variant_type": "",
            "image_url": "https://cdn.example.com/alice.png",
        }
    ]
    assert "Alice" in brief
    assert "Bob" not in brief
    assert "/api/character-images" not in brief


def test_comic_agent_prepares_page_generation_with_filtered_references_and_seed() -> None:
    regen_task = {
        "prompt_context_hash": "context-hash",
        "prompt_request_summary": {"page_character_names": ["Alice"]},
        "character_image_references": [
            {"name": "Alice", "variant_key": "default", "image_url": "https://cdn.example.com/alice.png"},
            {"name": "Bob", "variant_key": "default", "image_url": "https://cdn.example.com/bob.png"},
            {"name": "Alice", "variant_key": "local", "image_url": "/api/character-images/characters/alice/image"},
        ],
    }

    plan = ComicWorkflowAgent.prepare_page_generation(
        project_id="project-1",
        chapter_number=2,
        page_number=3,
        original_prompt_text="prompt",
        regen_task=regen_task,
        provider_profile={"supports_seed": True, "supports_reference_images": True},
    )

    assert isinstance(plan["seed"], int)
    assert plan["page_character_names"] == ["Alice"]
    assert plan["reference_image_count"] == 1
    assert plan["reference_images"][0]["name"] == "Alice"


def test_character_image_agent_derives_seed_only_when_profile_supports_it() -> None:
    plan = CharacterImageWorkflowAgent.prepare_variant_generation(
        project_id="project-1",
        character_id="char-1",
        variant_key="default",
        prompt_text="prompt",
        provider_profile={"supports_seed": True},
    )
    fallback = CharacterImageWorkflowAgent.prepare_variant_generation(
        project_id="project-1",
        character_id="char-1",
        variant_key="default",
        prompt_text="prompt",
        provider_profile={"supports_seed": False},
    )

    assert isinstance(plan["seed"], int)
    assert plan["consistency_key"] == "project-1:char-1:default"
    assert fallback["seed"] is None
