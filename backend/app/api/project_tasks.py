"""Project-level background task aggregation API."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.common import verify_project_access
from app.api import chapters as chapters_api
from app.api import characters as characters_api
from app.api import comics as comics_api
from app.api import outlines as outlines_api
from app.database import get_db
from app.models.batch_generation_task import BatchGenerationTask

router = APIRouter(prefix="/projects", tags=["项目任务"])

ACTIVE_STATUSES = {"pending", "queued", "running", "generating", "processing"}


def _parse_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str) and value.strip():
        normalized = value.strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(normalized)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def _iso(value: Any) -> str | None:
    parsed = _parse_dt(value)
    return parsed.isoformat() if parsed else None


def _elapsed_seconds(task: dict[str, Any]) -> int | None:
    start = _parse_dt(task.get("started_at")) or _parse_dt(task.get("created_at"))
    if not start:
        return None
    end = _parse_dt(task.get("completed_at")) if str(task.get("status")) not in ACTIVE_STATUSES else None
    end = end or datetime.now(timezone.utc)
    return max(0, int((end - start).total_seconds()))


def _progress_percent(completed: Any, total: Any, fallback: Any = None) -> int:
    if fallback is not None:
        try:
            return max(0, min(100, int(fallback)))
        except (TypeError, ValueError):
            pass
    try:
        total_int = int(total or 0)
        completed_int = int(completed or 0)
    except (TypeError, ValueError):
        return 0
    if total_int <= 0:
        return 0
    return max(0, min(100, round(completed_int / total_int * 100)))


def _normalize_task(
    *,
    task_id: str,
    task_type: str,
    title: str,
    status: str,
    total: Any = None,
    completed: Any = None,
    progress: Any = None,
    current: str | None = None,
    message: str | None = None,
    error_message: str | None = None,
    created_at: Any = None,
    started_at: Any = None,
    updated_at: Any = None,
    completed_at: Any = None,
    details: dict[str, Any] | None = None,
    errors: list[Any] | None = None,
) -> dict[str, Any]:
    raw = {
        "status": status,
        "created_at": created_at,
        "started_at": started_at,
        "updated_at": updated_at,
        "completed_at": completed_at,
    }
    return {
        "task_id": task_id,
        "type": task_type,
        "title": title,
        "status": status,
        "running": status in ACTIVE_STATUSES,
        "total": total,
        "completed": completed,
        "progress": _progress_percent(completed, total, progress),
        "current": current,
        "message": message,
        "error_message": error_message,
        "created_at": _iso(created_at),
        "started_at": _iso(started_at),
        "updated_at": _iso(updated_at),
        "completed_at": _iso(completed_at),
        "elapsed_seconds": _elapsed_seconds(raw),
        "details": details or {},
        "errors": errors or [],
    }


def _status_sort_key(task: dict[str, Any]) -> tuple[int, float]:
    status_rank = 0 if task.get("running") else 1
    timestamp = _parse_dt(task.get("updated_at") or task.get("created_at"))
    return (status_rank, -(timestamp.timestamp() if timestamp else 0))


def _collect_comic_page_tasks(project_id: str) -> list[dict[str, Any]]:
    state = comics_api._load_regen_state(project_id)
    latest_by_target = state.get("latest_by_target")
    if not isinstance(latest_by_target, dict):
        return []

    tasks: list[dict[str, Any]] = []
    for task in latest_by_target.values():
        if not isinstance(task, dict):
            continue
        if task.get("project_id") != project_id:
            continue
        task_type = "comic_page_edit" if task.get("target_type") == "page_edit" else "comic_page_regenerate"
        action = "漫画改图" if task_type == "comic_page_edit" else "漫画重生图"
        chapter_number = task.get("chapter_number")
        page_number = task.get("page_number")
        tasks.append(
            _normalize_task(
                task_id=str(task.get("task_id") or f"{chapter_number}-{page_number}"),
                task_type=task_type,
                title=f"{action}：第 {chapter_number} 章 / 第 {page_number} 页",
                status=str(task.get("status") or "pending"),
                total=1,
                completed=1 if task.get("status") == "completed" else 0,
                progress=100 if task.get("status") == "completed" else (50 if task.get("status") == "running" else 0),
                current=f"第 {chapter_number} 章，第 {page_number} 页",
                message=task.get("prompt_request_summary"),
                error_message=task.get("worker_error"),
                created_at=task.get("created_at"),
                updated_at=task.get("updated_at"),
                completed_at=task.get("updated_at") if task.get("status") in {"completed", "failed"} else None,
                details={
                    "chapter_number": chapter_number,
                    "page_number": page_number,
                    "character_image_reference_count": task.get("character_image_reference_count"),
                },
            )
        )
    return tasks


def _collect_comic_batch_tasks(project_id: str) -> list[dict[str, Any]]:
    state = comics_api._load_comic_batch_state(project_id)
    latest = state.get("latest_by_task")
    tasks: list[dict[str, Any]] = []
    if isinstance(latest, dict):
        for raw_task in latest.values():
            if not isinstance(raw_task, dict):
                continue
            task_id = str(raw_task.get("task_id") or "")
            task = comics_api._latest_comic_batch_task(project_id, task_id) or raw_task
            if task.get("project_id") != project_id:
                continue
            tasks.append(
                _normalize_task(
                    task_id=task_id,
                    task_type="comic_batch",
                    title="批量漫画生成",
                    status=str(task.get("status") or "pending"),
                    total=task.get("total"),
                    completed=task.get("completed"),
                    current=f"第 {task.get('current_chapter_number')} 章" if task.get("current_chapter_number") else None,
                    created_at=task.get("created_at"),
                    updated_at=task.get("updated_at"),
                    completed_at=task.get("completed_at"),
                    error_message=task.get("error_message") or task.get("error"),
                    details={"chapter_numbers": task.get("chapter_numbers"), "options": task.get("options")},
                    errors=list(task.get("errors") or []),
                )
            )
    return tasks


def _collect_pipeline_tasks(project_id: str) -> list[dict[str, Any]]:
    state = comics_api._load_pipeline_batch_state(project_id)
    latest = state.get("latest_by_task")
    tasks: list[dict[str, Any]] = []
    if isinstance(latest, dict):
        for raw_task in latest.values():
            if not isinstance(raw_task, dict):
                continue
            task_id = str(raw_task.get("task_id") or "")
            task = comics_api._latest_pipeline_batch_task(project_id, task_id) or raw_task
            if task.get("project_id") != project_id:
                continue
            current_stage = task.get("current_stage")
            tasks.append(
                _normalize_task(
                    task_id=task_id,
                    task_type="full_pipeline",
                    title="批量全流程",
                    status=str(task.get("status") or "pending"),
                    total=task.get("total"),
                    completed=task.get("completed"),
                    current=f"第 {task.get('current_chapter_number')} 章 · {current_stage}" if task.get("current_chapter_number") else str(current_stage or ""),
                    created_at=task.get("created_at"),
                    started_at=task.get("started_at"),
                    updated_at=task.get("updated_at"),
                    completed_at=task.get("completed_at"),
                    error_message=task.get("error_message"),
                    details={
                        "generation_mode": task.get("generation_mode"),
                        "current_stage": current_stage,
                        "stages": task.get("stages"),
                        "options": task.get("options"),
                    },
                    errors=list(task.get("errors") or []),
                )
            )
    return tasks


def _collect_storyboard_tasks(project_id: str) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    for task in comics_api._storyboard_gen_tasks.values():
        if not isinstance(task, dict) or task.get("project_id") != project_id:
            continue
        task_type = "storyboard_batch" if task.get("type") == "batch" else "storyboard"
        title = "批量分镜生成" if task_type == "storyboard_batch" else f"分镜生成：第 {task.get('chapter_number')} 章"
        tasks.append(
            _normalize_task(
                task_id=str(task.get("task_id") or ""),
                task_type=task_type,
                title=title,
                status=str(task.get("status") or "pending"),
                total=task.get("total") or 1,
                completed=task.get("completed") or 0,
                current=f"第 {task.get('current_chapter_number')} 章" if task.get("current_chapter_number") else None,
                created_at=task.get("created_at"),
                updated_at=task.get("updated_at"),
                completed_at=task.get("updated_at") if task.get("status") in {"completed", "failed"} else None,
                error_message=task.get("error"),
                details={"chapter_numbers": task.get("chapter_numbers"), "result": task.get("result")},
                errors=list(task.get("errors") or []),
            )
        )
    return tasks


def _collect_outline_tasks(project_id: str, user_id: str | None) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    for task in outlines_api._batch_outline_expansion_tasks.values():
        if not isinstance(task, dict):
            continue
        if task.get("project_id") != project_id or task.get("user_id") != user_id:
            continue
        tasks.append(
            _normalize_task(
                task_id=str(task.get("task_id") or ""),
                task_type="outline_batch_expand",
                title="批量生成大纲章节",
                status=str(task.get("status") or "pending"),
                total=task.get("total"),
                completed=task.get("completed"),
                progress=task.get("progress"),
                current=task.get("current_outline_title"),
                message=task.get("message"),
                created_at=task.get("created_at"),
                started_at=task.get("started_at"),
                updated_at=task.get("updated_at"),
                completed_at=task.get("completed_at"),
                error_message=task.get("error"),
                details={"total_chapters_created": task.get("total_chapters_created")},
                errors=list(task.get("failed_outlines") or []),
            )
        )
    return tasks


def _collect_visual_bible_task(project_id: str) -> list[dict[str, Any]]:
    task = characters_api.BIBLE_BATCH_STATE.get(f"bible_batch:{project_id}")
    if not isinstance(task, dict) or task.get("status") == "idle":
        return []
    total = int(task.get("total") or 0)
    completed = int(task.get("completed") or 0) + int(task.get("failed") or 0)
    return [
        _normalize_task(
            task_id=f"bible_batch:{project_id}",
            task_type="visual_bible_batch",
            title="批量角色视觉圣经",
            status=str(task.get("status") or "pending"),
            total=total,
            completed=completed,
            current=task.get("current_character_name"),
            created_at=task.get("created_at"),
            started_at=task.get("started_at"),
            updated_at=task.get("updated_at"),
            completed_at=task.get("completed_at"),
            error_message=task.get("error"),
            details={"failed": task.get("failed")},
        )
    ]


async def _collect_chapter_batch_tasks(project_id: str, user_id: str | None, db: AsyncSession) -> list[dict[str, Any]]:
    result = await db.execute(
        select(BatchGenerationTask)
        .where(BatchGenerationTask.project_id == project_id)
        .where(BatchGenerationTask.user_id == user_id)
        .order_by(BatchGenerationTask.created_at.desc())
        .limit(10)
    )
    tasks: list[dict[str, Any]] = []
    for task in result.scalars().all():
        tasks.append(
            _normalize_task(
                task_id=task.id,
                task_type="chapter_batch",
                title="批量章节生成",
                status=task.status or "pending",
                total=task.total_chapters,
                completed=task.completed_chapters,
                current=f"第 {task.current_chapter_number} 章" if task.current_chapter_number else None,
                created_at=task.created_at,
                started_at=task.started_at,
                completed_at=task.completed_at,
                error_message=task.error_message,
                details={
                    "start_chapter_number": task.start_chapter_number,
                    "chapter_count": task.chapter_count,
                    "current_retry_count": task.current_retry_count,
                    "max_retries": task.max_retries,
                    "active_in_runtime": task.id in chapters_api.active_batch_generation_tasks,
                },
                errors=list(task.failed_chapters or []),
            )
        )
    return tasks


@router.get("/{project_id}/tasks", summary="聚合项目后台任务")
async def get_project_tasks(
    project_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user_id = getattr(request.state, "user_id", None)
    await verify_project_access(project_id, user_id, db)

    tasks: list[dict[str, Any]] = []
    tasks.extend(await _collect_chapter_batch_tasks(project_id, user_id, db))
    tasks.extend(_collect_outline_tasks(project_id, user_id))
    tasks.extend(_collect_storyboard_tasks(project_id))
    tasks.extend(_collect_comic_page_tasks(project_id))
    tasks.extend(_collect_comic_batch_tasks(project_id))
    tasks.extend(_collect_pipeline_tasks(project_id))
    tasks.extend(_collect_visual_bible_task(project_id))

    tasks = sorted(tasks, key=_status_sort_key)
    running_count = sum(1 for task in tasks if task.get("running"))

    return {
        "project_id": project_id,
        "running_count": running_count,
        "total_count": len(tasks),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "tasks": tasks[:30],
    }
