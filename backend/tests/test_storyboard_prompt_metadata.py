from __future__ import annotations

import json
from pathlib import Path

from app.services.storyboard_prompt_cache import (
    is_storyboard_prompt_fresh,
    load_storyboard_prompt_metadata,
    storyboard_prompt_context_hash,
    storyboard_prompt_metadata_path,
    storyboard_prompt_request_summary,
    write_storyboard_prompt_metadata,
)


def _sample_page_data(*, turning_point: str = "turn") -> dict[str, object]:
    return {
        "page_goal": "让主角进入密室",
        "scene": "moonlit hall",
        "turning_point": turning_point,
        "panel_count": 2,
        "must_keep": ["silver key", "blue cloak"],
        "panels": [
            {"panel_number": 1, "characters": ["Alice"]},
            {"panel_number": 2, "characters": ["Alice", "Bob"]},
        ],
        "panel_plan": [
            {
                "panel_number": 1,
                "beat": "arrival",
                "visual_goal": "wide shot",
                "camera_intent": "low angle",
                "emotion": "tense",
                "dialogue_focus": "none",
            },
            {
                "panel_number": 2,
                "beat": "reveal",
                "visual_goal": "close-up",
                "camera_intent": "push in",
                "emotion": "surprised",
                "dialogue_focus": "key glints",
            },
        ],
    }


def test_storyboard_prompt_cache_roundtrip_and_refresh_detection(tmp_path: Path) -> None:
    prompt_path = tmp_path / "page_03_prompt.txt"
    prompt_path.write_text("prompt body\n", encoding="utf-8")

    metadata = {
        "prompt_version": 2,
        "context_hash": storyboard_prompt_context_hash(
            project_id="project-1",
            chapter_number=1,
            page_number=3,
            total_pages=8,
            page_data=_sample_page_data(),
            continuity_pack="continuity v1",
            page_context="previous / current / next",
            character_reference_brief="Alice | variant: default",
            comic_style_instruction="dense manhua style",
        ),
        "request_summary": storyboard_prompt_request_summary(
            project_title="Demo Project",
            chapter_number=1,
            chapter_title="Chapter One",
            page_number=3,
            page_data=_sample_page_data(),
            page_context="previous / current / next",
            character_reference_brief="Alice | variant: default",
            comic_style_instruction="dense manhua style",
        ),
    }
    write_storyboard_prompt_metadata(prompt_path, metadata)

    metadata_path = storyboard_prompt_metadata_path(prompt_path)
    assert metadata_path.name == "page_03_prompt.meta.json"
    assert metadata_path.is_file()

    loaded = load_storyboard_prompt_metadata(prompt_path)
    assert loaded == json.loads(metadata_path.read_text(encoding="utf-8"))
    assert loaded["request_summary"]["page_character_names"] == ["Alice", "Bob"]
    assert is_storyboard_prompt_fresh(loaded, context_hash=metadata["context_hash"], prompt_version=2) is True

    stale = dict(loaded or {})
    stale["prompt_version"] = 1
    metadata_path.write_text(json.dumps(stale, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    refreshed = load_storyboard_prompt_metadata(prompt_path)
    assert is_storyboard_prompt_fresh(refreshed, context_hash=metadata["context_hash"], prompt_version=2) is False

    new_hash = storyboard_prompt_context_hash(
        project_id="project-1",
        chapter_number=1,
        page_number=3,
        total_pages=8,
        page_data=_sample_page_data(turning_point="new turn"),
        continuity_pack="continuity v1",
        page_context="previous / current / next",
        character_reference_brief="Alice | variant: default",
        comic_style_instruction="dense manhua style",
    )
    assert new_hash != metadata["context_hash"]
