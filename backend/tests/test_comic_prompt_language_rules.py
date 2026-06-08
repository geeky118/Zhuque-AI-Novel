from __future__ import annotations

import app.database  # noqa: F401

from app.api.comics import _build_storyboard_page_prompt, _with_comic_safety_rules


def test_comic_safety_rules_use_configured_english_visible_text() -> None:
    prompt = _with_comic_safety_rules("A comic scene with speech bubbles.", image_text_language="en")

    assert "must be English only. Do not render Chinese characters." in prompt


def test_storyboard_page_prompt_uses_configured_english_visible_text() -> None:
    prompt = _build_storyboard_page_prompt(
        project_title="Demo Project",
        chapter_number=1,
        chapter_title="Chapter One",
        page_number=1,
        page_data={
            "panels": [
                {
                    "panel_number": 1,
                    "description": "A hero speaks.",
                    "scene": "street",
                    "characters": ["Alice"],
                    "camera_angle": "close-up",
                    "emotion": "tense",
                    "dialogue": "你好",
                }
            ]
        },
        comic_style_instruction="dense manhua style",
        image_text_language="en",
    )

    assert (
        "Visible text rule: any visible text inside the image, including speech bubbles, captions, signs and sound effects, "
        "must be English only. Do not render Chinese characters."
    ) in prompt
    assert (
        "Dialogue reference: use a short natural English speech bubble that conveys the emotion; "
        "do not render the source-language text."
    ) in prompt
    assert "画面文字规则：图像中的对话气泡、旁白、标牌和拟声词等可见文字必须使用简体中文" not in prompt
    assert "如果需要在画面中出现气泡文字，请保留或压缩为简短自然的简体中文。" not in prompt
    assert "如果需要在画面中出现气泡文字，请译写为简短自然的简体中文。" not in prompt


def test_storyboard_page_prompt_defaults_to_chinese_visible_text() -> None:
    prompt = _build_storyboard_page_prompt(
        project_title="Demo Project",
        chapter_number=1,
        chapter_title="Chapter One",
        page_number=1,
        page_data={
            "panels": [
                {
                    "panel_number": 1,
                    "description": "A hero speaks.",
                    "dialogue": "Hello",
                }
            ]
        },
        comic_style_instruction="dense manhua style",
    )

    assert "画面文字规则：图像中的对话气泡、旁白、标牌和拟声词等可见文字必须使用简体中文" in prompt
    assert "如果需要在画面中出现气泡文字，请译写为简短自然的简体中文。" in prompt
    assert "must be English only" not in prompt
