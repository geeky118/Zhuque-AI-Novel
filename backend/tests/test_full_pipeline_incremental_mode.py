from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from app.services.comic_pipeline_utils import (
    should_retry_comic_image_error,
    chapter_pipeline_context_summary,
    filter_missing_comic_page_numbers,
    resolve_analysis_stage_action,
)


def test_filter_missing_comic_page_numbers_skips_existing_local_and_cos_pages(tmp_path: Path) -> None:
    existing_page = tmp_path / "page_01.png"
    existing_page.write_bytes(b"png")
    manhua_scan = {
        "pages": {
            1: existing_page,
            2: tmp_path / "page_02.png",
            3: tmp_path / "page_03.png",
        }
    }
    page_artifacts = {
        (1, 1): SimpleNamespace(image_local_path=str(existing_page), image_cos_object_key=None),
        (1, 2): SimpleNamespace(image_local_path=None, image_cos_object_key=None),
        (1, 3): SimpleNamespace(image_local_path=None, image_cos_object_key="cos-object-key"),
    }

    assert filter_missing_comic_page_numbers([1, 2, 3], manhua_scan, page_artifacts, 1) == [2]


def test_chapter_pipeline_context_summary_prefers_summary_and_falls_back_to_content() -> None:
    chapter_with_summary = SimpleNamespace(
        chapter_number=4,
        title="归来",
        content="正文不应被优先使用",
        summary="  摘要优先  ",
    )
    chapter_with_content = SimpleNamespace(
        chapter_number=5,
        title="重逢",
        content="正文" * 200,
        summary=None,
    )

    assert chapter_pipeline_context_summary(chapter_with_summary) == "第4章《归来》：摘要优先"
    assert chapter_pipeline_context_summary(chapter_with_content).startswith("第5章《重逢》：正文正文正文")
    assert chapter_pipeline_context_summary(chapter_with_content).endswith("…")


def test_resolve_analysis_stage_action_skips_existing_only_in_incremental_mode() -> None:
    state = {"has_analysis": True, "analysis_id": "analysis-1", "has_active_task": False}

    assert resolve_analysis_stage_action("incremental", state) == {
        "action": "skip",
        "reason": "已有章节分析",
        "analysis_id": "analysis-1",
    }
    assert resolve_analysis_stage_action("full", state) == {"action": "run"}


def test_resolve_analysis_stage_action_skips_active_task_before_regeneration() -> None:
    state = {
        "has_analysis": False,
        "has_active_task": True,
        "task_id": "task-1",
        "task_status": "running",
    }

    assert resolve_analysis_stage_action("incremental", state) == {
        "action": "skip",
        "reason": "分析任务正在执行",
        "task_id": "task-1",
        "task_status": "running",
    }


def test_should_retry_comic_image_error_only_for_502_or_timeout() -> None:
    assert should_retry_comic_image_error(502, "HTTP 502: Bad Gateway")
    assert should_retry_comic_image_error(None, "request timeout while reading image bytes")
    assert not should_retry_comic_image_error(500, "HTTP 500: invalid_png_signature")
    assert not should_retry_comic_image_error(None, "invalid_png_signature")
