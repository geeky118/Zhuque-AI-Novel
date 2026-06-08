"""漫画全流程批量生成的纯判断辅助函数。"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any


COMIC_IMAGE_RETRY_STATUS_CODES = {429, 502, 503, 504}
COMIC_IMAGE_RETRY_DETAIL_PATTERN = re.compile(r"\b(?:http\s*)?(?:429|502|503|504)\b", re.IGNORECASE)
COMIC_IMAGE_RETRY_DETAIL_HINTS = (
    "timeout",
    "timed out",
    "upstream_error",
    "too many requests",
    "rate limit",
    "appchatreverse",
)


def page_has_image(page_path: Path | None, page_artifact: Any | None) -> bool:
    if page_artifact and (
        getattr(page_artifact, "image_cos_url", None)
        or getattr(page_artifact, "image_cos_object_key", None)
    ):
        return True
    local_path = page_path or (
        Path(getattr(page_artifact, "image_local_path", "")) if page_artifact and getattr(page_artifact, "image_local_path", None) else None
    )
    return bool(local_path and Path(local_path).is_file())


def chapter_has_content(chapter: Any | None) -> bool:
    return bool(chapter and getattr(chapter, "content", None) and str(chapter.content).strip())


def chapter_pipeline_context_summary(chapter: Any | None) -> str | None:
    if not chapter_has_content(chapter):
        return None
    chapter_number = getattr(chapter, "chapter_number", None)
    title = getattr(chapter, "title", None)
    summary_text = str(getattr(chapter, "summary", None) or "").strip()
    if not summary_text:
        summary_text = str(getattr(chapter, "content", "") or "").strip()
    if len(summary_text) > 300:
        summary_text = summary_text[:300].rstrip() + "…"
    if not summary_text:
        return None
    return f"第{chapter_number}章《{title}》：{summary_text}"


def resolve_analysis_stage_action(
    generation_mode: str,
    analysis_state: dict[str, Any] | None,
) -> dict[str, Any]:
    state = analysis_state or {}
    if state.get("has_active_task"):
        return {
            "action": "skip",
            "reason": "分析任务正在执行",
            "task_id": state.get("task_id"),
            "task_status": state.get("task_status"),
        }
    if generation_mode == "incremental" and state.get("has_analysis"):
        return {
            "action": "skip",
            "reason": "已有章节分析",
            "analysis_id": state.get("analysis_id"),
        }
    return {"action": "run"}


def filter_missing_comic_page_numbers(
    page_numbers: list[int],
    manhua_scan: dict[str, Any],
    page_artifacts: dict[tuple[int, int], Any],
    chapter_number: int,
) -> list[int]:
    missing_pages: list[int] = []
    scanned_pages = manhua_scan.get("pages", {})
    for page_number in page_numbers:
        raw_page_path = scanned_pages.get(page_number)
        page_path = Path(raw_page_path) if isinstance(raw_page_path, str) else raw_page_path
        page_artifact = page_artifacts.get((chapter_number, page_number))
        if not page_has_image(page_path, page_artifact):
            missing_pages.append(page_number)
    return missing_pages


def should_retry_comic_image_error(status_code: int | None, detail: str | None) -> bool:
    if status_code in COMIC_IMAGE_RETRY_STATUS_CODES:
        return True
    normalized_detail = str(detail or "").lower()
    if COMIC_IMAGE_RETRY_DETAIL_PATTERN.search(normalized_detail):
        return True
    return any(hint in normalized_detail for hint in COMIC_IMAGE_RETRY_DETAIL_HINTS)
