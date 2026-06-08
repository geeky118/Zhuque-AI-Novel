"""拆书导入服务：任务管理、预览构建与落库执行"""
from __future__ import annotations

import asyncio
import json
import re
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.common import verify_project_access
from app.config import DATA_DIR, settings as app_settings
from app.database import get_engine
from app.logger import get_logger
from app.models.chapter import Chapter
from app.models.character import Character
from app.models.career import Career, CharacterCareer
from app.models.foreshadow import Foreshadow
from app.models.analysis_task import AnalysisTask
from app.models.memory import StoryMemory
from app.models.mcp_plugin import MCPPlugin
from app.models.outline import Outline
from app.models.project import Project
from app.models.project_default_style import ProjectDefaultStyle
from app.models.relationship import CharacterRelationship, Organization, OrganizationMember, RelationshipType
from app.models.settings import Settings
from app.models.writing_style import WritingStyle
from app.schemas.book_import import (
    BookImportApplyRequest,
    BookImportApplyResponse,
    BookImportAnalysisDossier,
    BookImportChapter,
    BookImportExtractMode,
    BookImportExtractedCharacter,
    BookImportExtractedForeshadow,
    BookImportExtractedMemory,
    BookImportExtractedOrganization,
    BookImportExtractedRelationship,
    BookImportOutline,
    BookImportPreviewResponse,
    BookImportTaskCreateResponse,
    BookImportTaskStatusResponse,
    BookImportWarning,
    ProjectSuggestion,
)
from app.services.ai_service import AIService, create_user_ai_service_with_mcp
from app.services.prompt_service import PromptService
from app.services.txt_parser_service import txt_parser_service

logger = get_logger(__name__)

BOOK_IMPORT_TASK_STATE_DIR = DATA_DIR / "book_import_tasks"
BOOK_IMPORT_TASK_STALE_SECONDS = 15 * 60
BOOK_IMPORT_TASK_ID_RE = re.compile(r"^[0-9a-fA-F-]{36}$")
BOOK_IMPORT_TRANSIENT_AI_HINTS = (
    "429",
    "502",
    "503",
    "504",
    "timeout",
    "timed out",
    "service unavailable",
    "bad gateway",
    "gateway timeout",
    "upstream",
    "temporarily unavailable",
    "too many requests",
    "rate limit",
    "rate_limit",
    "rate_limit_exceeded",
    "no available tokens",
)
BOOK_IMPORT_LONG_RETRY_DELAYS = (15, 45, 120, 300, 600, 900, 1200)


@dataclass
class _StepFailure:
    """记录某个生成步骤的失败信息"""
    step_name: str          # 步骤标识: world_building / career_system / characters
    step_label: str         # 步骤中文名
    error_message: str      # 错误详情
    retry_count: int = 0    # 已重试次数


@dataclass
class _BookImportTask:
    task_id: str
    user_id: str
    filename: str
    project_id: Optional[str]
    create_new_project: bool
    import_mode: str
    extract_mode: BookImportExtractMode = "full"
    tail_chapter_count: int = 10
    status: str = "pending"
    progress: int = 0
    message: Optional[str] = "任务已创建"
    error: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    preview: Optional[BookImportPreviewResponse] = None
    cancelled: bool = False
    # 导入后生成的 project_id，用于重试时定位项目
    imported_project_id: Optional[str] = None
    # 步骤级失败记录
    failed_steps: list[_StepFailure] = field(default_factory=list)


class BookImportService:
    """拆书导入服务（内存任务 + 本地快照，避免轮询跨进程时丢任务）"""

    def __init__(self) -> None:
        self._tasks: dict[str, _BookImportTask] = {}
        self._tasks_lock = asyncio.Lock()
        self._post_import_analysis_tasks: set[asyncio.Task] = set()
        self._post_import_followup_tasks: dict[str, asyncio.Task] = {}
        self._post_import_followup_states: dict[str, dict[str, Any]] = {}
        BOOK_IMPORT_TASK_STATE_DIR.mkdir(parents=True, exist_ok=True)

    def _is_transient_ai_error(self, exc: Exception) -> bool:
        response = getattr(exc, "response", None)
        status_code = getattr(response, "status_code", None) or getattr(exc, "status_code", None)
        if status_code in {429, 502, 503, 504}:
            return True
        detail = str(exc).lower()
        return any(hint in detail for hint in BOOK_IMPORT_TRANSIENT_AI_HINTS)

    def _ai_retry_delay(self, attempt: int, exc: Exception) -> int:
        detail = str(exc).lower()
        if "429" in detail or "too many requests" in detail or "rate limit" in detail:
            base = 60
        else:
            base = BOOK_IMPORT_LONG_RETRY_DELAYS[min(attempt - 1, len(BOOK_IMPORT_LONG_RETRY_DELAYS) - 1)]
        return min(max(base, BOOK_IMPORT_LONG_RETRY_DELAYS[min(attempt - 1, len(BOOK_IMPORT_LONG_RETRY_DELAYS) - 1)]), 1800)

    async def _call_ai_json_with_resilient_retry(
        self,
        ai_service: AIService,
        *,
        prompt: str,
        expected_type: str,
        label: str,
        max_attempts: int = 6,
        json_retries: int = 3,
    ) -> Any:
        last_error: Exception | None = None
        for attempt in range(1, max_attempts + 1):
            try:
                return await ai_service.call_with_json_retry(
                    prompt=prompt,
                    max_retries=json_retries,
                    expected_type=expected_type,
                )
            except Exception as exc:
                last_error = exc
                if attempt >= max_attempts or not self._is_transient_ai_error(exc):
                    raise
                wait_seconds = self._ai_retry_delay(attempt, exc)
                logger.warning(
                    "拆书导入 %s 遇到临时 AI 上游错误，准备重试: attempt=%s/%s wait=%ss error=%s",
                    label,
                    attempt,
                    max_attempts,
                    wait_seconds,
                    exc,
                )
                await asyncio.sleep(wait_seconds)
        if last_error:
            raise last_error
        raise RuntimeError(f"拆书导入 {label} AI 调用失败")

    async def create_task(
        self,
        *,
        user_id: str,
        filename: str,
        file_content: bytes,
        project_id: Optional[str],
        create_new_project: bool,
        import_mode: str,
        extract_mode: BookImportExtractMode = "full",
        tail_chapter_count: int = 10,
    ) -> BookImportTaskCreateResponse:
        normalized_tail_count = max(5, int(tail_chapter_count))
        normalized_extract_mode = extract_mode
        if normalized_tail_count % 5 != 0:
            normalized_tail_count = ((normalized_tail_count + 4) // 5) * 5
        if normalized_tail_count > 50:
            normalized_extract_mode = "full"

        task_id = str(uuid.uuid4())
        task = _BookImportTask(
            task_id=task_id,
            user_id=user_id,
            filename=filename,
            project_id=project_id,
            create_new_project=create_new_project,
            import_mode=import_mode,
            extract_mode=normalized_extract_mode,
            tail_chapter_count=normalized_tail_count,
        )
        async with self._tasks_lock:
            self._tasks[task_id] = task
        self._persist_task_snapshot(task)

        asyncio.create_task(self._run_pipeline(task_id=task_id, file_content=file_content))
        return BookImportTaskCreateResponse(task_id=task_id, status="pending")

    async def get_task_status(self, *, task_id: str, user_id: str) -> BookImportTaskStatusResponse:
        task = await self._get_task(task_id=task_id, user_id=user_id)
        return self._to_status(task)

    async def get_preview(self, *, task_id: str, user_id: str) -> BookImportPreviewResponse:
        task = await self._get_task(task_id=task_id, user_id=user_id)
        if task.status != "completed":
            raise HTTPException(status_code=400, detail="任务尚未完成，无法获取预览")
        if not task.preview:
            raise HTTPException(status_code=500, detail="预览数据不存在")
        return task.preview

    async def cancel_task(self, *, task_id: str, user_id: str) -> dict:
        task = await self._get_task(task_id=task_id, user_id=user_id)
        if task.status in {"completed", "failed", "cancelled"}:
            return {"success": True, "message": f"任务已是终态：{task.status}"}

        task.cancelled = True
        self._set_task_state(task, status="cancelled", progress=task.progress, message="任务已取消")
        return {"success": True, "message": "取消成功"}

    async def apply_import(
        self,
        *,
        task_id: str,
        user_id: str,
        payload: BookImportApplyRequest,
        db: AsyncSession,
    ) -> BookImportApplyResponse:
        task = await self._get_task(task_id=task_id, user_id=user_id)
        if task.status != "completed":
            raise HTTPException(status_code=400, detail="任务未完成，无法导入")

        statistics = {
            "chapters": 0,
            "outlines": 0,
        }

        warnings = list(task.preview.warnings) if task.preview else []
        chapters_to_import, outlines_to_import, was_trimmed = self._select_chapters_for_import(
            chapters=payload.chapters,
            outlines=payload.outlines,
            extract_mode=task.extract_mode,
            tail_chapter_count=task.tail_chapter_count,
        )
        if was_trimmed:
            warnings.append(
                BookImportWarning(
                    code="apply_trimmed_for_extract_mode",
                    message=f"导入阶段已按解析配置仅保留 {len(chapters_to_import)} 章",
                    level="info",
                )
            )

        try:
            project = await self._prepare_project(
                db=db,
                user_id=user_id,
                task=task,
                suggestion=payload.project_suggestion,
                chapters=chapters_to_import,
                import_mode=payload.import_mode,
            )

            outline_id_map = await self._import_outlines(
                db=db,
                project_id=project.id,
                outlines=outlines_to_import,
                import_mode=payload.import_mode,
            )
            statistics["outlines"] = len(outlines_to_import)

            chapter_count, words_delta, chapter_id_map = await self._import_chapters(
                db=db,
                project_id=project.id,
                chapters=chapters_to_import,
                outline_id_map=outline_id_map,
                import_mode=payload.import_mode,
            )
            statistics["chapters"] = chapter_count

            if payload.import_mode == "overwrite":
                project.current_words = words_delta
            else:
                project.current_words = (project.current_words or 0) + words_delta

            dossier_stats = await self._import_story_dossier(
                db=db,
                project=project,
                dossier=task.preview.analysis_dossier if task.preview else None,
                chapter_id_map=chapter_id_map,
            )
            statistics.update(dossier_stats)
            generated_entities = dossier_stats.get("generated_characters", 0) + dossier_stats.get("generated_organizations", 0)

            # TXT 导入项目已经有正文和反向大纲，不能再回到通用创作向导生成大纲。
            project.status = "writing"
            project.wizard_status = "completed"
            project.wizard_step = 4
            post_import_analysis_queue = await self._create_post_import_analysis_tasks(
                db=db,
                user_id=user_id,
                project_id=project.id,
                chapter_id_map=chapter_id_map,
            )
            statistics["analysis_tasks"] = len(post_import_analysis_queue)

            await db.commit()
            self._start_post_import_followup(
                user_id=user_id,
                project_id=project.id,
                character_count=max(project.character_count or 0, 8),
            )
            self._start_post_import_analysis(
                user_id=user_id,
                project_id=project.id,
                tasks_queue=post_import_analysis_queue,
            )

            return BookImportApplyResponse(
                success=True,
                project_id=project.id,
                statistics=statistics,
                warnings=warnings,
            )
        except HTTPException:
            await db.rollback()
            raise
        except Exception as exc:
            await db.rollback()
            logger.error(f"拆书导入落库失败: {exc}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"导入失败: {exc}")

    # ---- 类型别名：进度回调 ----
    ProgressCallback = Optional[Any]  # Callable[[str, int, str], Awaitable[None]]

    async def apply_import_stream(
        self,
        *,
        task_id: str,
        user_id: str,
        payload: BookImportApplyRequest,
        db: AsyncSession,
        progress_callback: Any = None,
    ) -> BookImportApplyResponse:
        """
        与 apply_import 相同的落库逻辑，但通过 progress_callback 推送细粒度进度。
        progress_callback(message: str, progress: int, status: str)
        """
        task = await self._get_task(task_id=task_id, user_id=user_id)
        if task.status != "completed":
            raise HTTPException(status_code=400, detail="任务未完成，无法导入")

        statistics: Dict[str, int] = {
            "chapters": 0,
            "outlines": 0,
        }

        warnings = list(task.preview.warnings) if task.preview else []
        chapters_to_import, outlines_to_import, was_trimmed = self._select_chapters_for_import(
            chapters=payload.chapters,
            outlines=payload.outlines,
            extract_mode=task.extract_mode,
            tail_chapter_count=task.tail_chapter_count,
        )
        if was_trimmed:
            warnings.append(
                BookImportWarning(
                    code="apply_trimmed_for_extract_mode",
                    message=f"导入阶段已按解析配置仅保留 {len(chapters_to_import)} 章",
                    level="info",
                )
            )

        async def _notify(message: str, progress: int, status: str = "processing") -> None:
            if progress_callback:
                await progress_callback(message, progress, status)

        try:
            # -- 步骤1: 创建项目 (0-5%)
            await _notify("正在创建项目...", 2)
            project = await self._prepare_project(
                db=db,
                user_id=user_id,
                task=task,
                suggestion=payload.project_suggestion,
                chapters=chapters_to_import,
                import_mode=payload.import_mode,
            )
            await _notify("项目创建完成", 5)

            # -- 步骤2: 导入大纲 (5-10%)
            await _notify("正在导入大纲...", 6)
            outline_id_map = await self._import_outlines(
                db=db,
                project_id=project.id,
                outlines=outlines_to_import,
                import_mode=payload.import_mode,
            )
            statistics["outlines"] = len(outlines_to_import)
            await _notify(f"已导入 {len(outlines_to_import)} 个大纲", 10)

            # -- 步骤3: 导入章节 (10-20%)
            await _notify(f"正在导入 {len(chapters_to_import)} 个章节...", 12)
            chapter_count, words_delta, chapter_id_map = await self._import_chapters(
                db=db,
                project_id=project.id,
                chapters=chapters_to_import,
                outline_id_map=outline_id_map,
                import_mode=payload.import_mode,
            )
            statistics["chapters"] = chapter_count

            if payload.import_mode == "overwrite":
                project.current_words = words_delta
            else:
                project.current_words = (project.current_words or 0) + words_delta
            await _notify(f"已导入 {chapter_count} 个章节（{words_delta}字）", 20)

            # -- 步骤4: 落库深度故事档案 (20-35%)
            failed_steps: list[_StepFailure] = []
            await _notify("正在写入人物、组织、关系和记忆档案...", 22)
            try:
                dossier_stats = await self._import_story_dossier(
                    db=db,
                    project=project,
                    dossier=task.preview.analysis_dossier if task.preview else None,
                    chapter_id_map=chapter_id_map,
                )
                statistics.update(dossier_stats)
                await _notify(
                    f"故事档案写入完成（角色{dossier_stats.get('generated_characters', 0)}，组织{dossier_stats.get('generated_organizations', 0)}，记忆{dossier_stats.get('generated_memories', 0)}）",
                    35,
                )
            except Exception as exc:
                logger.warning(f"拆书导入：故事档案写入失败（将继续后续步骤）: {exc}")
                failed_steps.append(_StepFailure(
                    step_name="story_dossier",
                    step_label="故事档案写入",
                    error_message=str(exc),
                ))
                await _notify(f"⚠️ 故事档案写入失败：{str(exc)[:80]}，将继续后续步骤", 35, "warning")

            # 基础导入完成后，将内容证据型补全与章节分析放到后台继续执行。
            # 这里必须直接标记向导完成，避免前端恢复到通用向导后虚构大纲/章节。
            project.wizard_step = 4
            project.wizard_status = "completed"
            project.status = "writing"

            post_import_analysis_queue = await self._create_post_import_analysis_tasks(
                db=db,
                user_id=user_id,
                project_id=project.id,
                chapter_id_map=chapter_id_map,
            )
            statistics["analysis_tasks"] = len(post_import_analysis_queue)

            await db.commit()
            await _notify("基础数据已保存，后台章节分析和项目补全已开始执行", 98)
            self._start_post_import_followup(
                user_id=user_id,
                project_id=project.id,
                character_count=max(project.character_count or 0, 8),
            )
            self._start_post_import_analysis(
                user_id=user_id,
                project_id=project.id,
                tasks_queue=post_import_analysis_queue,
            )

            # 记录失败步骤和项目ID到任务中，供重试使用
            task.imported_project_id = project.id
            task.failed_steps = failed_steps
            self._persist_task_snapshot(task)

            # 如果有步骤失败，通过 SSE 推送失败步骤详情
            if failed_steps:
                failed_info = [
                    {"step_name": f.step_name, "step_label": f.step_label, "error": f.error_message}
                    for f in failed_steps
                ]
                await _notify(
                    f"⚠️ 导入完成，但有 {len(failed_steps)} 个生成步骤失败，可点击重试",
                    98,
                    "warning",
                )
                # 通过特殊的 progress 消息推送失败步骤列表
                if progress_callback:
                    await progress_callback(
                        json.dumps({"failed_steps": failed_info}, ensure_ascii=False),
                        98,
                        "step_failures",
                    )

            return BookImportApplyResponse(
                success=True,
                project_id=project.id,
                statistics=statistics,
                warnings=warnings,
            )
        except HTTPException:
            await db.rollback()
            raise
        except Exception as exc:
            await db.rollback()
            logger.error(f"拆书导入落库失败: {exc}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"导入失败: {exc}")

    async def retry_failed_steps_stream(
        self,
        *,
        task_id: str,
        user_id: str,
        steps_to_retry: list[str],
        db: AsyncSession,
        progress_callback: Any = None,
    ) -> dict:
        """
        仅重试之前导入时失败的AI生成步骤。
        steps_to_retry: 需要重试的步骤名列表, 如 ["world_building", "career_system", "characters"]
        """
        task = await self._get_task(task_id=task_id, user_id=user_id)
        project_id = task.imported_project_id
        if not project_id:
            raise HTTPException(status_code=400, detail="该任务尚未完成导入，无法重试")

        # 验证 steps_to_retry 都是合法的失败步骤
        failed_step_names = {f.step_name for f in task.failed_steps}
        invalid_steps = [s for s in steps_to_retry if s not in failed_step_names]
        if invalid_steps:
            raise HTTPException(
                status_code=400,
                detail=f"以下步骤不在失败列表中，无法重试: {', '.join(invalid_steps)}",
            )

        async def _notify(message: str, progress: int, status: str = "processing") -> None:
            if progress_callback:
                await progress_callback(message, progress, status)

        try:
            from app.api.common import verify_project_access
            project = await verify_project_access(project_id, user_id, db)

            retry_results: dict[str, Any] = {}
            still_failed: list[_StepFailure] = []
            total_steps = len(steps_to_retry)

            for step_idx, step_name in enumerate(steps_to_retry):
                step_start_pct = int(5 + (step_idx / total_steps) * 85)
                step_end_pct = int(5 + ((step_idx + 1) / total_steps) * 85)

                # 查找原来的失败记录
                original_failure = next((f for f in task.failed_steps if f.step_name == step_name), None)
                retry_count = (original_failure.retry_count if original_failure else 0) + 1

                if step_name == "world_building":
                    await _notify("🔄 正在重试世界观生成...", step_start_pct)
                    try:
                        counts = await self._load_project_followup_counts(db=db, project_id=project.id)
                        if counts["chapters"] > 0:
                            result = await self._complete_world_from_imported_chapters(
                                db=db,
                                project=project,
                            )
                        else:
                            result = await self._generate_world_building_from_project(
                                db=db,
                                user_id=user_id,
                                project=project,
                                progress_callback=progress_callback,
                                progress_range=(step_start_pct, step_end_pct),
                                raise_on_error=True,
                            )
                        retry_results["generated_world_building"] = result
                        await _notify("✅ 世界观重试成功", step_end_pct)
                    except Exception as exc:
                        logger.warning(f"世界观重试失败 (第{retry_count}次): {exc}")
                        still_failed.append(_StepFailure(
                            step_name="world_building",
                            step_label="世界观生成",
                            error_message=str(exc),
                            retry_count=retry_count,
                        ))
                        await _notify(f"⚠️ 世界观重试失败：{str(exc)[:80]}", step_end_pct, "warning")

                elif step_name == "career_system":
                    await _notify("🔄 正在重试职业体系生成...", step_start_pct)
                    try:
                        counts = await self._load_project_followup_counts(db=db, project_id=project.id)
                        if counts["chapters"] > 0:
                            result = await self._generate_career_system_from_imported_chapters(
                                db=db,
                                user_id=user_id,
                                project=project,
                            )
                        else:
                            result = await self._generate_career_system_from_project(
                                db=db,
                                user_id=user_id,
                                project=project,
                                progress_callback=progress_callback,
                                progress_range=(step_start_pct, step_end_pct),
                            )
                        retry_results["generated_careers"] = result
                        await _notify(f"✅ 职业体系重试成功（{result}个）", step_end_pct)
                    except Exception as exc:
                        logger.warning(f"职业体系重试失败 (第{retry_count}次): {exc}")
                        still_failed.append(_StepFailure(
                            step_name="career_system",
                            step_label="职业体系生成",
                            error_message=str(exc),
                            retry_count=retry_count,
                        ))
                        await _notify(f"⚠️ 职业体系重试失败：{str(exc)[:80]}", step_end_pct, "warning")

                elif step_name == "characters":
                    character_count_target = max(project.character_count or 0, 5)
                    await _notify("🔄 正在重试角色与组织生成...", step_start_pct)
                    try:
                        counts = await self._load_project_followup_counts(db=db, project_id=project.id)
                        if counts["chapters"] > 0:
                            dossier_stats = await self._complete_story_dossier_from_imported_chapters(
                                db=db,
                                user_id=user_id,
                                project=project,
                            )
                            result = (
                                dossier_stats.get("generated_characters", 0)
                                + dossier_stats.get("generated_organizations", 0)
                                + dossier_stats.get("generated_relationships", 0)
                            )
                        else:
                            result = await self._generate_characters_and_organizations_from_project(
                                db=db,
                                user_id=user_id,
                                project=project,
                                count=character_count_target,
                                progress_callback=progress_callback,
                                progress_range=(step_start_pct, step_end_pct),
                            )
                        retry_results["generated_entities"] = result
                        await _notify(f"✅ 角色/组织重试成功（{result}个）", step_end_pct)
                    except Exception as exc:
                        logger.warning(f"角色/组织重试失败 (第{retry_count}次): {exc}")
                        still_failed.append(_StepFailure(
                            step_name="characters",
                            step_label="角色与组织生成",
                            error_message=str(exc),
                            retry_count=retry_count,
                        ))
                        await _notify(f"⚠️ 角色/组织重试失败：{str(exc)[:80]}", step_end_pct, "warning")

            # 提交数据库
            await _notify("正在保存到数据库...", 93)
            await db.commit()
            await _notify("数据保存完成", 96)

            # 更新任务的失败步骤记录
            task.failed_steps = still_failed
            self._persist_task_snapshot(task)

            if still_failed:
                failed_info = [
                    {"step_name": f.step_name, "step_label": f.step_label, "error": f.error_message, "retry_count": f.retry_count}
                    for f in still_failed
                ]
                if progress_callback:
                    await progress_callback(
                        json.dumps({"failed_steps": failed_info}, ensure_ascii=False),
                        98,
                        "step_failures",
                    )

            return {
                "success": True,
                "project_id": project_id,
                "retry_results": retry_results,
                "still_failed": [
                    {"step_name": f.step_name, "step_label": f.step_label, "error": f.error_message, "retry_count": f.retry_count}
                    for f in still_failed
                ],
            }
        except HTTPException:
            await db.rollback()
            raise
        except Exception as exc:
            await db.rollback()
            logger.error(f"拆书重试失败: {exc}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"重试失败: {exc}")

    async def _run_pipeline(self, *, task_id: str, file_content: bytes) -> None:
        task = self._tasks.get(task_id)
        if not task:
            return

        try:
            # 进度分配：编码识别 5%，文本清洗 10%，章节切分 15%，预览结构 20%，AI拆解 20%-99%，完成 100%
            self._set_task_state(task, status="running", progress=5, message="正在识别编码并读取文本...")
            self._check_cancelled(task)

            text, encoding = txt_parser_service.decode_bytes(file_content)
            cleaned = txt_parser_service.clean_text(text)

            self._set_task_state(task, status="running", progress=10, message=f"文本清洗完成（编码：{encoding}）")
            self._check_cancelled(task)

            chapters_data = txt_parser_service.split_chapters(cleaned)
            if not chapters_data:
                raise ValueError("未能识别到有效章节，请检查TXT内容")

            self._set_task_state(
                task, status="running", progress=15,
                message=f"已识别 {len(chapters_data)} 个章节，正在构建预览结构...",
            )
            self._check_cancelled(task)

            self._set_task_state(task, status="running", progress=18, message="正在按解析配置筛选章节并构建预览...")
            preview = await self._build_preview(
                task=task,
                filename=task.filename,
                task_id=task.task_id,
                chapters_data=chapters_data,
            )

            self._check_cancelled(task)
            task.preview = preview
            self._set_task_state(task, status="completed", progress=100, message="解析完成，可预览并确认导入")
        except asyncio.CancelledError:
            self._set_task_state(task, status="cancelled", progress=task.progress, message="任务已取消")
        except Exception as exc:
            logger.error(f"拆书任务失败 task_id={task_id}: {exc}", exc_info=True)
            self._set_task_state(
                task,
                status="failed",
                progress=task.progress,
                message="解析失败",
                error=str(exc),
            )

    async def _prepare_project(
        self,
        *,
        db: AsyncSession,
        user_id: str,
        task: _BookImportTask,
        suggestion: ProjectSuggestion,
        chapters: list[BookImportChapter],
        import_mode: str,
    ) -> Project:
        world_time_period, world_location, world_atmosphere, world_rules = self._derive_world_settings(
            suggestion=suggestion,
            chapters=chapters,
        )

        if task.create_new_project:
            project = Project(
                user_id=user_id,
                title=suggestion.title,
                description=suggestion.description,
                theme=suggestion.theme,
                genre=suggestion.genre,
                status="planning",
                wizard_status="incomplete",
                wizard_step=1,
                outline_mode="one-to-one",
                current_words=0,
                target_words=max(1000, int(suggestion.target_words or 100000)),
                narrative_perspective=(suggestion.narrative_perspective or "第三人称")[:50],
                world_time_period=world_time_period,
                world_location=world_location,
                world_atmosphere=world_atmosphere,
                world_rules=world_rules,
            )
            db.add(project)
            await db.flush()
            await self._ensure_project_default_style(db=db, project_id=project.id)
            return project

        if not task.project_id:
            raise HTTPException(status_code=400, detail="缺少目标项目ID")

        project = await verify_project_access(task.project_id, user_id, db)

        # 覆盖模式清空相关数据
        if import_mode == "overwrite":
            await self._clear_project_data(db=db, project_id=project.id)
            project.title = suggestion.title or project.title
            project.description = suggestion.description
            project.theme = suggestion.theme
            project.genre = suggestion.genre
            project.target_words = max(1000, int(suggestion.target_words or 100000))
            project.narrative_perspective = (suggestion.narrative_perspective or "第三人称")[:50]
            project.world_time_period = world_time_period
            project.world_location = world_location
            project.world_atmosphere = world_atmosphere
            project.world_rules = world_rules

        await self._ensure_project_default_style(db=db, project_id=project.id)
        return project

    async def _clear_project_data(self, *, db: AsyncSession, project_id: str) -> None:
        await db.execute(delete(Foreshadow).where(Foreshadow.project_id == project_id))
        await db.execute(delete(StoryMemory).where(StoryMemory.project_id == project_id))
        await db.execute(delete(Chapter).where(Chapter.project_id == project_id))
        await db.execute(delete(Outline).where(Outline.project_id == project_id))

        # 覆盖导入时统一清理角色相关链路，避免后续自动生成出现脏数据
        char_ids_result = await db.execute(select(Character.id).where(Character.project_id == project_id))
        char_ids = [row[0] for row in char_ids_result.fetchall()]

        await db.execute(delete(CharacterRelationship).where(CharacterRelationship.project_id == project_id))
        await db.execute(delete(OrganizationMember).where(OrganizationMember.character_id.in_(char_ids)))
        await db.execute(delete(Organization).where(Organization.project_id == project_id))
        await db.execute(delete(CharacterCareer).where(CharacterCareer.character_id.in_(char_ids)))
        await db.execute(delete(Career).where(Career.project_id == project_id))
        await db.execute(delete(Character).where(Character.project_id == project_id))

    async def _ensure_project_default_style(self, *, db: AsyncSession, project_id: str) -> None:
        """确保项目存在默认写作风格（缺失时自动设置为首个全局预设风格）。"""
        existing_result = await db.execute(
            select(ProjectDefaultStyle.style_id).where(ProjectDefaultStyle.project_id == project_id)
        )
        if existing_result.scalar_one_or_none() is not None:
            return

        preset_result = await db.execute(
            select(WritingStyle.id, WritingStyle.name)
            .where(WritingStyle.user_id.is_(None))
            .order_by(func.coalesce(WritingStyle.order_index, 999999), WritingStyle.id)
            .limit(1)
        )
        preset_row = preset_result.first()
        if not preset_row:
            logger.warning(f"项目 {project_id} 未找到可用全局预设风格，跳过默认风格设置")
            return

        style_id, style_name = preset_row
        db.add(ProjectDefaultStyle(project_id=project_id, style_id=style_id))
        logger.info(f"项目 {project_id} 自动设置默认写作风格: {style_name}(id={style_id})")

    async def _import_outlines(
        self,
        *,
        db: AsyncSession,
        project_id: str,
        outlines: list[BookImportOutline],
        import_mode: str,
    ) -> dict[str, str]:
        if not outlines:
            return {}

        existing_max_order = 0
        if import_mode == "append":
            res = await db.execute(select(func.max(Outline.order_index)).where(Outline.project_id == project_id))
            existing_max_order = res.scalar_one() or 0

        title_to_id: dict[str, str] = {}
        for idx, item in enumerate(outlines, start=1):
            outline_content = item.content
            if not outline_content and item.structure and isinstance(item.structure, dict):
                outline_content = str(item.structure.get("summary") or item.structure.get("content") or "").strip()

            outline = Outline(
                project_id=project_id,
                title=item.title,
                content=outline_content,
                structure=json.dumps(item.structure, ensure_ascii=False) if item.structure else None,
                order_index=(existing_max_order + idx),
            )
            db.add(outline)
            await db.flush()
            title_to_id[item.title] = outline.id

        return title_to_id

    async def _import_chapters(
        self,
        *,
        db: AsyncSession,
        project_id: str,
        chapters: list[BookImportChapter],
        outline_id_map: dict[str, str],
        import_mode: str,
    ) -> tuple[int, int, dict[int, str]]:
        if not chapters:
            return 0, 0, {}

        chapter_number_offset = 0
        if import_mode == "append":
            res = await db.execute(select(func.max(Chapter.chapter_number)).where(Chapter.project_id == project_id))
            chapter_number_offset = res.scalar_one() or 0

        count = 0
        total_words = 0
        chapter_id_map: dict[int, str] = {}
        for item in sorted(chapters, key=lambda x: x.chapter_number):
            chapter_number = chapter_number_offset + item.chapter_number
            word_count = len(item.content or "")

            chapter = Chapter(
                project_id=project_id,
                title=item.title,
                content=item.content,
                summary=item.summary,
                chapter_number=chapter_number,
                word_count=word_count,
                status="draft",
                outline_id=outline_id_map.get(item.outline_title or ""),
                sub_index=1,
            )
            db.add(chapter)
            await db.flush()
            chapter_id_map[item.chapter_number] = chapter.id
            count += 1
            total_words += word_count

        return count, total_words, chapter_id_map

    async def _import_story_dossier(
        self,
        *,
        db: AsyncSession,
        project: Project,
        dossier: Optional[BookImportAnalysisDossier],
        chapter_id_map: dict[int, str],
    ) -> dict[str, int]:
        """将深度拆书档案落库到角色、组织、关系、记忆与伏笔。"""
        stats = {
            "generated_characters": 0,
            "generated_organizations": 0,
            "generated_relationships": 0,
            "generated_memories": 0,
            "generated_foreshadows": 0,
        }
        if not dossier:
            return stats

        def _json(value: Any) -> Optional[str]:
            return json.dumps(value, ensure_ascii=False) if value else None

        def _status(value: Optional[str], allowed: set[str], fallback: str) -> str:
            normalized = (value or "").strip()
            return normalized if normalized in allowed else fallback

        def _safe_int(value: Any, default: int, min_value: int, max_value: int) -> int:
            try:
                parsed = int(value)
            except (TypeError, ValueError):
                parsed = default
            return max(min_value, min(parsed, max_value))

        existing_result = await db.execute(select(Character).where(Character.project_id == project.id))
        existing_characters = existing_result.scalars().all()
        character_by_name: dict[str, Character] = {item.name: item for item in existing_characters}

        # 组织也需要作为 Character 记录存在，先创建组织角色，再创建普通角色。
        for org_item in dossier.organizations:
            if org_item.name in character_by_name:
                continue
            character = Character(
                project_id=project.id,
                name=org_item.name,
                is_organization=True,
                role_type="supporting",
                personality=org_item.purpose,
                background=org_item.background,
                appearance=org_item.location,
                organization_type=org_item.organization_type,
                organization_purpose=org_item.purpose,
                organization_members=_json([member.get("name") for member in org_item.members if isinstance(member, dict)]),
                traits=_json(org_item.traits),
            )
            db.add(character)
            await db.flush()
            character_by_name[character.name] = character
            db.add(
                Organization(
                    character_id=character.id,
                    project_id=project.id,
                    power_level=org_item.power_level,
                    member_count=0,
                    location=org_item.location,
                    motto=org_item.purpose[:200] if org_item.purpose else None,
                )
            )
            stats["generated_organizations"] += 1

        for char_item in dossier.characters:
            if char_item.name in character_by_name:
                character = character_by_name[char_item.name]
                character.personality = self._merge_text(character.personality, char_item.personality, 1500)
                character.background = self._merge_text(character.background, char_item.background, 1800)
                character.appearance = character.appearance or char_item.appearance
                character.current_state = char_item.current_state or character.current_state
                character.state_updated_chapter = char_item.first_seen_chapter or character.state_updated_chapter
                if char_item.traits and not character.traits:
                    character.traits = _json(char_item.traits)
                continue

            character = Character(
                project_id=project.id,
                name=char_item.name,
                age=char_item.age,
                gender=char_item.gender,
                is_organization=False,
                role_type=char_item.role_type,
                personality=char_item.personality,
                background=char_item.background,
                appearance=char_item.appearance,
                current_state=char_item.current_state,
                state_updated_chapter=char_item.first_seen_chapter,
                traits=_json(char_item.traits),
            )
            db.add(character)
            await db.flush()
            character_by_name[character.name] = character
            stats["generated_characters"] += 1

        org_result = await db.execute(
            select(Organization, Character.name)
            .join(Character, Organization.character_id == Character.id)
            .where(Organization.project_id == project.id)
        )
        organization_by_name: dict[str, Organization] = {
            name: org for org, name in org_result.all() if name
        }

        member_pairs: set[tuple[str, str]] = set()
        member_result = await db.execute(
            select(OrganizationMember.organization_id, OrganizationMember.character_id)
            .join(Organization, OrganizationMember.organization_id == Organization.id)
            .where(Organization.project_id == project.id)
        )
        member_pairs.update((row[0], row[1]) for row in member_result.all())

        for org_item in dossier.organizations:
            org = organization_by_name.get(org_item.name)
            if not org:
                continue
            for member in org_item.members:
                if not isinstance(member, dict):
                    continue
                member_name = str(member.get("name") or "").strip()
                member_char = character_by_name.get(member_name)
                if not member_char or member_char.is_organization:
                    continue
                pair = (org.id, member_char.id)
                if pair in member_pairs:
                    continue
                db.add(
                    OrganizationMember(
                        organization_id=org.id,
                        character_id=member_char.id,
                        position=(str(member.get("position") or "成员").strip() or "成员")[:100],
                        rank=_safe_int(member.get("rank"), 0, 0, 10),
                        status=_status(str(member.get("status") or "active"), {"active", "retired", "expelled", "deceased"}, "active"),
                        loyalty=_safe_int(member.get("loyalty"), 50, 0, 100),
                        source="imported",
                    )
                )
                org.member_count = (org.member_count or 0) + 1
                member_pairs.add(pair)

        relationship_type_result = await db.execute(select(RelationshipType))
        relationship_type_map = {item.name: item.id for item in relationship_type_result.scalars().all()}
        existing_rel_result = await db.execute(
            select(CharacterRelationship.character_from_id, CharacterRelationship.character_to_id, CharacterRelationship.relationship_name)
            .where(CharacterRelationship.project_id == project.id)
        )
        relationship_keys = {(row[0], row[1], row[2]) for row in existing_rel_result.all()}

        for rel_item in dossier.relationships:
            source = character_by_name.get(rel_item.source)
            target = character_by_name.get(rel_item.target)
            if not source or not target or source.id == target.id:
                continue
            key = (source.id, target.id, rel_item.relationship_type)
            if key in relationship_keys:
                continue
            db.add(
                CharacterRelationship(
                    project_id=project.id,
                    character_from_id=source.id,
                    character_to_id=target.id,
                    relationship_type_id=relationship_type_map.get(rel_item.relationship_type),
                    relationship_name=rel_item.relationship_type,
                    intimacy_level=rel_item.intimacy_level,
                    status=_status(rel_item.status, {"active", "broken", "past", "complicated"}, "active"),
                    description=rel_item.description,
                    source="imported",
                )
            )
            relationship_keys.add(key)
            stats["generated_relationships"] += 1

        for memory_item in dossier.memories:
            chapter_id = chapter_id_map.get(memory_item.chapter_number)
            db.add(
                StoryMemory(
                    project_id=project.id,
                    chapter_id=chapter_id,
                    memory_type=memory_item.memory_type,
                    title=memory_item.title,
                    content=memory_item.content,
                    full_context=memory_item.content,
                    related_characters=[
                        character_by_name[name].id
                        for name in memory_item.related_characters
                        if name in character_by_name
                    ],
                    related_locations=memory_item.related_locations,
                    tags=memory_item.tags,
                    importance_score=memory_item.importance_score,
                    story_timeline=memory_item.chapter_number,
                    text_length=len(memory_item.content),
                    is_foreshadow=1 if memory_item.memory_type == "foreshadow" else 0,
                )
            )
            stats["generated_memories"] += 1

        foreshadow_statuses = {"pending", "planted", "resolved", "partially_resolved", "abandoned"}
        for item in dossier.foreshadows:
            plant_number = item.plant_chapter_number or 1
            target_number = item.target_resolve_chapter_number
            db.add(
                Foreshadow(
                    project_id=project.id,
                    title=item.title,
                    content=item.content,
                    hint_text=item.content[:500],
                    source_type="analysis",
                    plant_chapter_id=chapter_id_map.get(plant_number),
                    plant_chapter_number=plant_number,
                    target_resolve_chapter_id=chapter_id_map.get(target_number) if target_number else None,
                    target_resolve_chapter_number=target_number,
                    status=_status(item.status, foreshadow_statuses, "planted"),
                    is_long_term=bool(target_number and target_number - plant_number >= 5),
                    importance=item.importance,
                    strength=item.strength,
                    subtlety=item.subtlety,
                    related_characters=item.related_characters,
                    tags=item.tags,
                    category=item.category,
                    notes="TXT导入深度拆解自动抽取",
                )
            )
            stats["generated_foreshadows"] += 1

        await db.flush()
        return stats

    def _select_chapters_for_import(
        self,
        *,
        chapters: list[BookImportChapter],
        outlines: list[BookImportOutline],
        extract_mode: BookImportExtractMode,
        tail_chapter_count: int,
    ) -> tuple[list[BookImportChapter], list[BookImportOutline], bool]:
        if not chapters:
            return [], [], False

        sorted_chapters = sorted(chapters, key=lambda x: x.chapter_number)
        normalized_tail_count = max(5, int(tail_chapter_count))
        if normalized_tail_count > 50 or extract_mode == "full":
            selected = sorted_chapters
        else:
            normalized_tail_count = min(normalized_tail_count, len(sorted_chapters))
            selected = sorted_chapters[-normalized_tail_count:]

        was_trimmed = len(sorted_chapters) > len(selected)

        normalized_chapters: list[BookImportChapter] = []
        for idx, item in enumerate(selected, start=1):
            normalized_chapters.append(
                BookImportChapter(
                    title=item.title,
                    content=item.content,
                    summary=item.summary,
                    chapter_number=idx,
                    outline_title=item.outline_title or item.title,
                )
            )

        normalized_outlines: list[BookImportOutline] = []
        sorted_outlines = sorted(outlines, key=lambda x: x.order_index) if outlines else []
        if sorted_outlines:
            if extract_mode == "full":
                selected_outlines = sorted_outlines[:len(normalized_chapters)]
            else:
                selected_outlines = sorted_outlines[-len(normalized_chapters):]
            for idx, item in enumerate(selected_outlines, start=1):
                normalized_outlines.append(
                    BookImportOutline(
                        title=item.title,
                        content=item.content,
                        order_index=idx,
                        structure=item.structure,
                    )
                )

        while len(normalized_outlines) < len(normalized_chapters):
            chapter = normalized_chapters[len(normalized_outlines)]
            normalized_outlines.append(
                BookImportOutline(
                    title=chapter.outline_title or chapter.title,
                    content=chapter.summary,
                    order_index=len(normalized_outlines) + 1,
                    structure=self._build_fallback_outline_structure(chapter),
                )
            )

        for idx in range(min(len(normalized_chapters), len(normalized_outlines))):
            normalized_chapters[idx].outline_title = normalized_outlines[idx].title

        return normalized_chapters, normalized_outlines, was_trimmed

    def _select_raw_chapters_for_preview(
        self,
        *,
        chapters_data: list[dict],
        extract_mode: BookImportExtractMode,
        tail_chapter_count: int,
    ) -> tuple[list[dict], bool]:
        if not chapters_data:
            return [], False

        normalized_tail_count = max(5, int(tail_chapter_count))
        if normalized_tail_count > 50 or extract_mode == "full":
            return chapters_data, False

        normalized_tail_count = min(normalized_tail_count, len(chapters_data))

        selected = chapters_data[-normalized_tail_count:]
        return selected, len(selected) < len(chapters_data)

    def _get_extract_mode_label(self, extract_mode: BookImportExtractMode, selected_total: int) -> str:
        if extract_mode == "full" or selected_total > 50:
            return "整本"
        return f"末{selected_total}章"

    def _derive_world_settings(
        self,
        *,
        suggestion: ProjectSuggestion,
        chapters: list[BookImportChapter],
    ) -> tuple[str, str, str, str]:
        """根据拆书内容推断基础世界设定，确保新建项目有可用初始值。"""
        sample_parts: list[str] = [
            suggestion.title or "",
            suggestion.theme or "",
            suggestion.genre or "",
            suggestion.description or "",
        ]
        for chapter in chapters[:3]:
            if chapter.content:
                sample_parts.append(chapter.content[:1200])

        sample_text = "\n".join(sample_parts)
        genre = suggestion.genre or ""
        theme = suggestion.theme or ""

        time_period = self._detect_time_period(sample_text, genre)
        location = self._detect_location(sample_text, genre)
        atmosphere = self._detect_atmosphere(sample_text, genre, theme)
        rules = self._detect_world_rules(sample_text, genre)

        return time_period, location, atmosphere, rules

    def _detect_time_period(self, text: str, genre: str) -> str:
        if any(k in text for k in ("民国", "军阀", "北洋", "租界")):
            return "近代民国时期"
        if any(k in text for k in ("星际", "宇宙", "机甲", "赛博", "未来", "人工智能")):
            return "未来科技时代"
        if any(k in text for k in ("古代", "王朝", "皇帝", "后宫", "朝堂", "将军", "宗门", "修仙", "江湖", "武林")):
            return "古代架空时代"
        if any(k in text for k in ("校园", "大学", "高中", "公司", "都市", "地铁")):
            return "现代都市"

        if any(k in genre for k in ("科幻", "星际")):
            return "未来科技时代"
        if any(k in genre for k in ("仙侠", "玄幻", "武侠", "历史", "古言")):
            return "古代架空时代"
        return "现代都市（可在世界设定页调整）"

    def _detect_location(self, text: str, genre: str) -> str:
        if any(k in text for k in ("星际", "宇宙", "舰队", "空间站", "机甲")):
            return "多星系宇宙与舰队文明"
        if any(k in text for k in ("宗门", "仙门", "秘境", "灵脉", "江湖", "武林")):
            return "宗门林立的江湖/仙侠世界"
        if any(k in text for k in ("王朝", "都城", "皇宫", "边关", "朝堂")):
            return "王朝都城与边疆并存的古代世界"
        if any(k in text for k in ("校园", "大学", "高中")):
            return "校园与城市生活场景"
        if any(k in text for k in ("都市", "城市", "街区", "公司", "医院")):
            return "现代城市社会"

        if "悬疑" in genre:
            return "现代城市与封闭场景并行"
        return "以人物活动区域为核心的现实场景"

    def _detect_atmosphere(self, text: str, genre: str, theme: str) -> str:
        if any(k in text for k in ("悬疑", "谜", "诡", "凶案", "惊悚", "追查")):
            return "紧张悬疑、危机渐进"
        if any(k in text for k in ("热血", "战斗", "对决", "复仇", "战争")):
            return "高压对抗、节奏强烈"
        if any(k in text for k in ("治愈", "日常", "温馨", "轻松", "搞笑")):
            return "日常细腻、轻松温暖"
        if any(k in text for k in ("权谋", "宫斗", "朝堂", "家族斗争")):
            return "权谋博弈、暗流涌动"

        if "言情" in genre:
            return "情感拉扯、细腻克制"
        if theme:
            return f"{theme}导向、人物驱动"
        return "人物驱动、冲突递进"

    def _detect_world_rules(self, text: str, genre: str) -> str:
        if any(k in text for k in ("修仙", "玄幻", "灵气", "境界", "宗门", "飞升")) or any(k in genre for k in ("仙侠", "玄幻")):
            return "存在修炼体系与等级秩序，资源与传承决定势力格局。"
        if any(k in text for k in ("星际", "机甲", "赛博", "人工智能", "基因")) or any(k in genre for k in ("科幻", "星际")):
            return "科技规则主导社会运行，组织制度与技术能力决定角色行动边界。"
        if any(k in text for k in ("江湖", "门派", "武林", "侠客")) or "武侠" in genre:
            return "江湖门派秩序与恩怨规则并行，强者与名望影响话语权。"
        if any(k in text for k in ("王朝", "皇权", "朝堂", "礼法")) or any(k in genre for k in ("历史", "古言")):
            return "以礼法与权力秩序为基础，家国与阶层关系深刻影响人物命运。"
        return "以现实逻辑为基础，结合剧情推进逐步补充特殊设定。"

    def _strip_chapter_prefix(self, title: str) -> str:
        """移除章节标题前缀“第X章/节/回/卷”，保留真实标题。"""
        normalized = (title or "").strip()
        if not normalized:
            return normalized

        stripped = re.sub(
            r"^第\s*[0-9零一二三四五六七八九十百千万两〇]+\s*[章节回卷]\s*[-—:：、.．）)】\]]*\s*",
            "",
            normalized,
        ).strip()

        return stripped or normalized

    async def _build_preview(
        self,
        *,
        task: _BookImportTask,
        filename: str,
        task_id: str,
        chapters_data: list[dict],
    ) -> BookImportPreviewResponse:
        suggestion = ProjectSuggestion(
            title=Path(filename).stem[:200] or "拆书导入项目",
            description="由拆书功能自动生成，可在导入前修改",
            theme=None,
            genre=None,
            narrative_perspective="第三人称",
            target_words=100000,
        )

        chapters: list[BookImportChapter] = []
        warnings: list[BookImportWarning] = []

        selected_chapters_raw, was_trimmed = self._select_raw_chapters_for_preview(
            chapters_data=chapters_data,
            extract_mode=task.extract_mode,
            tail_chapter_count=task.tail_chapter_count,
        )
        selected_total = len(selected_chapters_raw)
        selection_label = self._get_extract_mode_label(task.extract_mode, selected_total)

        title_counter: Counter[str] = Counter()
        for idx, chapter in enumerate(selected_chapters_raw, start=1):
            raw_title = (chapter.get("title") or f"第{idx}章").strip()[:200]
            title = self._strip_chapter_prefix(raw_title)[:200]
            content = (chapter.get("content") or "").strip()
            summary = self._build_summary(content)

            chapters.append(
                BookImportChapter(
                    title=title,
                    content=content,
                    summary=summary,
                    chapter_number=idx,
                    outline_title=title,
                )
            )

            title_counter[title] += 1
            if len(content) < 300:
                warnings.append(
                    BookImportWarning(
                        code="chapter_too_short",
                        message=f"章节「{title}」内容较短，建议检查切分结果",
                        level="warning",
                    )
                )
            if len(content) > 12000:
                warnings.append(
                    BookImportWarning(
                        code="chapter_too_long",
                        message=f"章节「{title}」内容较长，建议确认是否应继续拆分",
                        level="info",
                    )
                )

            # 章节构建进度：18% -> 20%（在这个区间内按比例推进）
            chapter_progress = 18 + int(2 * idx / max(1, selected_total))
            if idx % max(1, selected_total // 5) == 0 or idx == selected_total:
                self._set_task_state(
                    task,
                    status="running",
                    progress=chapter_progress,
                    message=f"已处理{selection_label} {idx}/{selected_total} 个章节结构...",
                )

        for title, count in title_counter.items():
            if count > 1:
                warnings.append(
                    BookImportWarning(
                        code="duplicate_chapter_title",
                        message=f"检测到重复章节标题「{title}」共 {count} 次",
                        level="warning",
                    )
                )

        if was_trimmed:
            warnings.append(
                BookImportWarning(
                    code="trimmed_for_extract_mode",
                    message=f"已按解析配置仅保留{selection_label} {selected_total} 章用于导入（原始识别 {len(chapters_data)} 章）",
                    level="info",
                )
            )

        # AI 反向生成项目信息：进度 20% -> 95%
        self._set_task_state(
            task,
            status="running",
            progress=20,
            message="正在调用AI反向生成项目信息（标题/简介/主题/类型）...",
        )
        suggestion = await self._generate_reverse_project_suggestion(
            user_id=task.user_id,
            suggestion=suggestion,
            chapters=chapters,
            task=task,
        )

        outlines = await self._generate_reverse_outlines(
            user_id=task.user_id,
            suggestion=suggestion,
            chapters=chapters,
            task=task,
        )

        analysis_dossier = await self._generate_deep_story_dossier(
            user_id=task.user_id,
            suggestion=suggestion,
            chapters=chapters,
            outlines=outlines,
            task=task,
        )

        return BookImportPreviewResponse(
            task_id=task_id,
            project_suggestion=suggestion,
            chapters=chapters,
            outlines=outlines,
            warnings=warnings,
            analysis_dossier=analysis_dossier,
        )

    async def _generate_reverse_project_suggestion(
        self,
        *,
        user_id: str,
        suggestion: ProjectSuggestion,
        chapters: list[BookImportChapter],
        task: Optional[_BookImportTask] = None,
    ) -> ProjectSuggestion:
        """
        基于前3章内容反向生成项目信息：
        小说简介、主题、类型、叙事角度、目标字数（默认10W）。
        进度区间：20% -> 95%
        """
        fallback = self._build_fallback_project_suggestion(
            title=suggestion.title,
            chapters=chapters,
        )

        sampled_chapters = chapters[:3]
        sampled_text = "\n\n".join(
            f"【第{idx + 1}章 {chapter.title}】\n{(chapter.content or '')[:2000]}"
            for idx, chapter in enumerate(sampled_chapters)
        ).strip()

        if not sampled_text:
            if task:
                self._set_task_state(task, status="running", progress=95, message="文本样本不足，使用规则推断项目信息")
            return fallback

        try:
            if task:
                self._set_task_state(task, status="running", progress=25, message="正在初始化AI服务...")

            engine = await get_engine(user_id)
            session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
            async with session_factory() as db:
                ai_service = await self._build_user_ai_service(db=db, user_id=user_id)

                if task:
                    self._set_task_state(task, status="running", progress=30, message="正在准备AI提示词...")

                template = await PromptService.get_template("BOOK_IMPORT_REVERSE_PROJECT_SUGGESTION", user_id, db)
                prompt = PromptService.format_prompt(
                    template,
                    title=suggestion.title or "拆书导入项目",
                    sampled_text=sampled_text,
                )

                if task:
                    self._set_task_state(task, status="running", progress=35, message="AI正在分析文本内容...")

                # 启动一个模拟进度推进的协程，在AI调用期间持续更新进度
                ai_done = asyncio.Event()

                async def _progress_ticker() -> None:
                    """在AI生成期间，每2秒推进一次进度（35% -> 85%）"""
                    if not task:
                        return
                    current = 35
                    messages = [
                        "AI正在分析文本内容...",
                        "AI正在识别故事主题与类型...",
                        "AI正在推断叙事角度...",
                        "AI正在生成项目简介...",
                        "AI正在整理生成结果...",
                    ]
                    msg_idx = 0
                    while not ai_done.is_set() and current < 85:
                        await asyncio.sleep(2)
                        if ai_done.is_set():
                            break
                        current = min(current + 5, 85)
                        msg = messages[min(msg_idx, len(messages) - 1)]
                        msg_idx += 1
                        self._set_task_state(task, status="running", progress=current, message=msg)

                ticker_task = asyncio.create_task(_progress_ticker())

                try:
                    project_data = await self._call_ai_json_with_resilient_retry(
                        ai_service,
                        prompt=prompt,
                        expected_type="object",
                        label="项目信息反推",
                        max_attempts=4,
                    )
                finally:
                    ai_done.set()
                    await ticker_task

                if task:
                    self._set_task_state(task, status="running", progress=90, message="AI生成完成，正在整理项目信息...")

                result = ProjectSuggestion(
                    title=suggestion.title,
                    description=(project_data.get("description") or fallback.description or "").strip(),
                    theme=(project_data.get("theme") or fallback.theme or "").strip() or fallback.theme,
                    genre=(project_data.get("genre") or fallback.genre or "").strip() or fallback.genre,
                    narrative_perspective=self._extract_narrative_perspective(
                        project_data,
                        fallback.narrative_perspective,
                    ),
                    target_words=self._normalize_target_words(
                        project_data.get("target_words"),
                        fallback.target_words,
                    ),
                )

                if task:
                    self._set_task_state(task, status="running", progress=95, message="项目信息生成完毕，准备预览...")

                return result
        except Exception as exc:
            logger.warning(f"反向生成项目信息失败，回退规则推断: {exc}")
            if task:
                self._set_task_state(task, status="running", progress=95, message="AI生成失败，使用规则推断项目信息")
            return fallback

    async def _generate_reverse_outlines(
        self,
        *,
        user_id: str,
        suggestion: ProjectSuggestion,
        chapters: list[BookImportChapter],
        task: Optional[_BookImportTask] = None,
    ) -> list[BookImportOutline]:
        """
        基于导入章节反向生成对应大纲，严格对齐现有 OUTLINE_CREATE 结构。
        采用单批次5章分批生成，避免一次性上下文过大。
        """
        if not chapters:
            return []

        fallback_outlines = [
            BookImportOutline(
                title=chapter.title,
                content=(chapter.summary or self._build_summary(chapter.content or "")),
                order_index=chapter.chapter_number,
                structure=self._build_fallback_outline_structure(chapter),
            )
            for chapter in chapters
        ]

        try:
            if task:
                self._set_task_state(task, status="running", progress=95, message="正在反向生成章节大纲（分批5章）...")

            engine = await get_engine(user_id)
            session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
            async with session_factory() as db:
                ai_service = await self._build_user_ai_service(db=db, user_id=user_id)
                template = await PromptService.get_template("BOOK_IMPORT_REVERSE_OUTLINES", user_id, db)

                batch_size = 5
                total_batches = (len(chapters) + batch_size - 1) // batch_size
                all_structures: list[dict[str, Any]] = []

                for batch_idx, start in enumerate(range(0, len(chapters), batch_size), start=1):
                    batch = chapters[start: start + batch_size]
                    if not batch:
                        continue

                    start_chapter = batch[0].chapter_number
                    end_chapter = batch[-1].chapter_number
                    chapters_text = self._build_reverse_outline_chapters_text(batch)
                    expected_count = len(batch)

                    if task:
                        progress = 95 + int(3 * (batch_idx - 1) / max(1, total_batches))
                        self._set_task_state(
                            task,
                            status="running",
                            progress=progress,
                            message=f"正在生成大纲批次 {batch_idx}/{total_batches}（第{start_chapter}-{end_chapter}章）...",
                        )

                    prompt = PromptService.format_prompt(
                        template,
                        title=suggestion.title or "拆书导入项目",
                        genre=suggestion.genre or "通用",
                        theme=suggestion.theme or "未设定",
                        narrative_perspective=suggestion.narrative_perspective or "第三人称",
                        start_chapter=start_chapter,
                        end_chapter=end_chapter,
                        expected_count=expected_count,
                        chapters_text=chapters_text,
                    )

                    ai_data = await self._call_ai_json_with_resilient_retry(
                        ai_service,
                        prompt=prompt,
                        expected_type="array",
                        label=f"大纲反推第{start_chapter}-{end_chapter}章",
                        max_attempts=4,
                    )
                    normalized_batch = self._normalize_reverse_outline_batch(ai_data, batch)
                    all_structures.extend(normalized_batch)

                if len(all_structures) != len(chapters):
                    logger.warning(
                        f"反向大纲数量与章节数量不一致，回退校正: outlines={len(all_structures)}, chapters={len(chapters)}"
                    )
                    all_structures = [
                        self._build_fallback_outline_structure(chapter)
                        for chapter in chapters
                    ]

                outlines = [
                    BookImportOutline(
                        title=chapter.title,
                        content=str((structure.get("summary") or structure.get("content") or "")).strip(),
                        order_index=chapter.chapter_number,
                        structure=structure,
                    )
                    for chapter, structure in zip(chapters, all_structures)
                ]

                if task:
                    self._set_task_state(task, status="running", progress=99, message="大纲反向生成完成，正在整理预览...")

                return outlines
        except Exception as exc:
            logger.warning(f"反向生成章节大纲失败，回退规则大纲: {exc}")
            if task:
                self._set_task_state(task, status="running", progress=99, message="AI大纲生成失败，使用规则大纲")
            return fallback_outlines

    async def _generate_deep_story_dossier(
        self,
        *,
        user_id: str,
        suggestion: ProjectSuggestion,
        chapters: list[BookImportChapter],
        outlines: list[BookImportOutline],
        task: Optional[_BookImportTask] = None,
    ) -> BookImportAnalysisDossier:
        """分批抽取人物、关系、组织、记忆和伏笔，形成可落库故事档案。"""
        fallback = self._build_fallback_story_dossier(chapters=chapters, outlines=outlines)
        if not chapters:
            return fallback

        try:
            if task:
                self._set_task_state(task, status="running", progress=99, message="正在分批抽取人物、关系、组织和记忆...")

            engine = await get_engine(user_id)
            session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
            async with session_factory() as db:
                ai_service = await self._build_user_ai_service(db=db, user_id=user_id)
                template = await PromptService.get_template("BOOK_IMPORT_DEEP_STORY_DOSSIER", user_id, db)

                batch_size = 4
                total_batches = (len(chapters) + batch_size - 1) // batch_size
                merged = BookImportAnalysisDossier()

                for batch_idx, start in enumerate(range(0, len(chapters), batch_size), start=1):
                    batch = chapters[start:start + batch_size]
                    if not batch:
                        continue

                    start_chapter = batch[0].chapter_number
                    end_chapter = batch[-1].chapter_number
                    if task:
                        progress = 99
                        self._set_task_state(
                            task,
                            status="running",
                            progress=progress,
                            message=f"故事档案智能体正在分析第 {start_chapter}-{end_chapter} 章（{batch_idx}/{total_batches}）...",
                        )

                    prompt = PromptService.format_prompt(
                        template,
                        title=suggestion.title or "拆书导入项目",
                        genre=suggestion.genre or "通用",
                        theme=suggestion.theme or "未设定",
                        narrative_perspective=suggestion.narrative_perspective or "第三人称",
                        start_chapter=start_chapter,
                        end_chapter=end_chapter,
                        chapters_text=self._build_deep_dossier_chapters_text(batch),
                    )

                    raw = await self._call_ai_json_with_resilient_retry(
                        ai_service,
                        prompt=prompt,
                        expected_type="object",
                        label=f"故事档案抽取第{start_chapter}-{end_chapter}章",
                        max_attempts=4,
                    )
                    if isinstance(raw, dict):
                        self._merge_story_dossier(merged, self._normalize_story_dossier(raw, batch))

                self._merge_story_dossier(merged, fallback, prefer_existing=True)
                return merged
        except Exception as exc:
            logger.warning(f"深度故事档案抽取失败，回退规则档案: {exc}")
            if task:
                self._set_task_state(task, status="running", progress=99, message="深度档案抽取失败，使用规则记忆兜底")
            return fallback

    def _build_deep_dossier_chapters_text(self, chapters: list[BookImportChapter]) -> str:
        parts: list[str] = []
        for chapter in chapters:
            content = (chapter.content or "").strip()
            if len(content) > 5200:
                content = f"{content[:3600]}\n\n……\n\n{content[-1400:]}"
            parts.append(
                f"【第{chapter.chapter_number}章 {chapter.title}】\n"
                f"摘要：{chapter.summary or '无'}\n"
                f"正文：\n{content or '无'}"
            )
        return "\n\n".join(parts)

    def _normalize_story_dossier(
        self,
        raw: dict[str, Any],
        chapters: list[BookImportChapter],
    ) -> BookImportAnalysisDossier:
        valid_chapter_numbers = {chapter.chapter_number for chapter in chapters}
        fallback_chapter = chapters[0].chapter_number if chapters else 1

        def _list_of_strings(value: Any, limit: int = 8) -> list[str]:
            if not isinstance(value, list):
                return []
            return [str(item).strip()[:200] for item in value if str(item).strip()][:limit]

        def _float(value: Any, default: float = 0.5) -> float:
            try:
                parsed = float(value)
            except (TypeError, ValueError):
                parsed = default
            return max(0.0, min(parsed, 1.0))

        def _int(value: Any, default: int, min_value: int, max_value: int) -> int:
            try:
                parsed = int(value)
            except (TypeError, ValueError):
                parsed = default
            return max(min_value, min(parsed, max_value))

        characters: list[BookImportExtractedCharacter] = []
        for item in raw.get("characters") if isinstance(raw.get("characters"), list) else []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not self._is_valid_entity_name(name):
                continue
            first_seen = item.get("first_seen_chapter")
            try:
                first_seen_int = int(first_seen) if first_seen is not None else None
            except (TypeError, ValueError):
                first_seen_int = None
            if first_seen_int not in valid_chapter_numbers:
                first_seen_int = fallback_chapter
            characters.append(
                BookImportExtractedCharacter(
                    name=name[:100],
                    role_type=(str(item.get("role_type") or "supporting").strip() or "supporting")[:50],
                    gender=(str(item.get("gender")).strip()[:50] if item.get("gender") else None),
                    age=(str(item.get("age")).strip()[:50] if item.get("age") else None),
                    personality=(str(item.get("personality")).strip()[:1000] if item.get("personality") else None),
                    background=(str(item.get("background")).strip()[:1200] if item.get("background") else None),
                    appearance=(str(item.get("appearance")).strip()[:500] if item.get("appearance") else None),
                    current_state=(str(item.get("current_state")).strip()[:800] if item.get("current_state") else None),
                    traits=_list_of_strings(item.get("traits"), 10),
                    first_seen_chapter=first_seen_int,
                    importance=_float(item.get("importance"), 0.5),
                )
            )

        relationships: list[BookImportExtractedRelationship] = []
        for item in raw.get("relationships") if isinstance(raw.get("relationships"), list) else []:
            if not isinstance(item, dict):
                continue
            source = str(item.get("source") or "").strip()
            target = str(item.get("target") or "").strip()
            if not self._is_valid_entity_name(source) or not self._is_valid_entity_name(target) or source == target:
                continue
            relationships.append(
                BookImportExtractedRelationship(
                    source=source[:100],
                    target=target[:100],
                    relationship_type=(str(item.get("relationship_type") or "关联").strip() or "关联")[:100],
                    intimacy_level=_int(item.get("intimacy_level"), 50, -100, 100),
                    status=(str(item.get("status") or "active").strip() or "active")[:20],
                    description=(str(item.get("description")).strip()[:1000] if item.get("description") else None),
                )
            )

        organizations: list[BookImportExtractedOrganization] = []
        for item in raw.get("organizations") if isinstance(raw.get("organizations"), list) else []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not self._is_valid_entity_name(name):
                continue
            members = item.get("members") if isinstance(item.get("members"), list) else []
            normalized_members = [m for m in members if isinstance(m, dict) and self._is_valid_entity_name(str(m.get("name") or "").strip())][:30]
            organizations.append(
                BookImportExtractedOrganization(
                    name=name[:100],
                    organization_type=(str(item.get("organization_type")).strip()[:100] if item.get("organization_type") else None),
                    purpose=(str(item.get("purpose")).strip()[:500] if item.get("purpose") else None),
                    background=(str(item.get("background")).strip()[:1200] if item.get("background") else None),
                    location=(str(item.get("location")).strip()[:500] if item.get("location") else None),
                    power_level=_int(item.get("power_level"), 50, 0, 100),
                    members=normalized_members,
                    traits=_list_of_strings(item.get("traits"), 10),
                )
            )

        memories: list[BookImportExtractedMemory] = []
        for item in raw.get("memories") if isinstance(raw.get("memories"), list) else []:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "").strip()
            content = str(item.get("content") or "").strip()
            if not title or not content:
                continue
            chapter_number = _int(item.get("chapter_number"), fallback_chapter, 1, 999999)
            if chapter_number not in valid_chapter_numbers:
                chapter_number = fallback_chapter
            memory_type = str(item.get("memory_type") or "plot_point").strip()
            if memory_type not in {"plot_point", "character_event", "world_detail", "hook", "foreshadow", "dialogue", "scene"}:
                memory_type = "plot_point"
            memories.append(
                BookImportExtractedMemory(
                    title=title[:200],
                    content=content[:1500],
                    memory_type=memory_type,
                    chapter_number=chapter_number,
                    related_characters=_list_of_strings(item.get("related_characters"), 12),
                    related_locations=_list_of_strings(item.get("related_locations"), 8),
                    tags=_list_of_strings(item.get("tags"), 10),
                    importance_score=_float(item.get("importance_score"), 0.5),
                )
            )

        foreshadows: list[BookImportExtractedForeshadow] = []
        for item in raw.get("foreshadows") if isinstance(raw.get("foreshadows"), list) else []:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "").strip()
            content = str(item.get("content") or "").strip()
            if not title or not content:
                continue
            plant = item.get("plant_chapter_number")
            target = item.get("target_resolve_chapter_number")
            try:
                plant_number = int(plant) if plant is not None else fallback_chapter
            except (TypeError, ValueError):
                plant_number = fallback_chapter
            try:
                target_number = int(target) if target is not None else None
            except (TypeError, ValueError):
                target_number = None
            foreshadows.append(
                BookImportExtractedForeshadow(
                    title=title[:200],
                    content=content[:1500],
                    plant_chapter_number=max(1, plant_number),
                    target_resolve_chapter_number=max(1, target_number) if target_number else None,
                    status=(str(item.get("status") or "planted").strip() or "planted")[:20],
                    category=(str(item.get("category")).strip()[:50] if item.get("category") else None),
                    related_characters=_list_of_strings(item.get("related_characters"), 12),
                    tags=_list_of_strings(item.get("tags"), 10),
                    importance=_float(item.get("importance"), 0.5),
                    strength=_int(item.get("strength"), 5, 1, 10),
                    subtlety=_int(item.get("subtlety"), 5, 1, 10),
                )
            )

        return BookImportAnalysisDossier(
            source_platform_notes=_list_of_strings(raw.get("source_platform_notes"), 10),
            world_notes=_list_of_strings(raw.get("world_notes"), 20),
            characters=characters,
            relationships=relationships,
            organizations=organizations,
            memories=memories,
            foreshadows=foreshadows,
        )

    def _merge_story_dossier(
        self,
        target: BookImportAnalysisDossier,
        incoming: BookImportAnalysisDossier,
        *,
        prefer_existing: bool = False,
    ) -> None:
        def _append_unique(values: list[str], incoming_values: list[str], limit: int) -> None:
            seen = set(values)
            for value in incoming_values:
                if value and value not in seen and len(values) < limit:
                    values.append(value)
                    seen.add(value)

        _append_unique(target.source_platform_notes, incoming.source_platform_notes, 30)
        _append_unique(target.world_notes, incoming.world_notes, 80)

        char_by_name = {item.name: item for item in target.characters}
        for item in incoming.characters:
            existing = char_by_name.get(item.name)
            if existing:
                if not prefer_existing:
                    existing.personality = self._merge_text(existing.personality, item.personality, 1200)
                    existing.background = self._merge_text(existing.background, item.background, 1500)
                    existing.appearance = existing.appearance or item.appearance
                    existing.current_state = item.current_state or existing.current_state
                    existing.importance = max(existing.importance, item.importance)
                    existing.traits = list(dict.fromkeys([*existing.traits, *item.traits]))[:12]
                    if not existing.first_seen_chapter or (item.first_seen_chapter and item.first_seen_chapter < existing.first_seen_chapter):
                        existing.first_seen_chapter = item.first_seen_chapter
                continue
            if len(target.characters) < 80:
                target.characters.append(item)
                char_by_name[item.name] = item

        org_by_name = {item.name: item for item in target.organizations}
        for item in incoming.organizations:
            existing = org_by_name.get(item.name)
            if existing:
                if not prefer_existing:
                    existing.purpose = existing.purpose or item.purpose
                    existing.background = self._merge_text(existing.background, item.background, 1500)
                    existing.location = existing.location or item.location
                    existing.power_level = max(existing.power_level, item.power_level)
                    existing.traits = list(dict.fromkeys([*existing.traits, *item.traits]))[:12]
                    existing.members = [*existing.members, *item.members][:50]
                continue
            if len(target.organizations) < 40:
                target.organizations.append(item)
                org_by_name[item.name] = item

        relationship_keys = {(item.source, item.target, item.relationship_type) for item in target.relationships}
        for item in incoming.relationships:
            key = (item.source, item.target, item.relationship_type)
            if key not in relationship_keys and len(target.relationships) < 160:
                target.relationships.append(item)
                relationship_keys.add(key)

        memory_keys = {(item.chapter_number, item.title) for item in target.memories}
        for item in incoming.memories:
            key = (item.chapter_number, item.title)
            if key not in memory_keys and len(target.memories) < 300:
                target.memories.append(item)
                memory_keys.add(key)

        foreshadow_keys = {(item.plant_chapter_number, item.title) for item in target.foreshadows}
        for item in incoming.foreshadows:
            key = (item.plant_chapter_number, item.title)
            if key not in foreshadow_keys and len(target.foreshadows) < 120:
                target.foreshadows.append(item)
                foreshadow_keys.add(key)

    def _build_fallback_story_dossier(
        self,
        *,
        chapters: list[BookImportChapter],
        outlines: list[BookImportOutline],
    ) -> BookImportAnalysisDossier:
        memories: list[BookImportExtractedMemory] = []
        for chapter in chapters[:80]:
            memories.append(
                BookImportExtractedMemory(
                    title=f"第{chapter.chapter_number}章：{chapter.title}",
                    content=(chapter.summary or self._build_summary(chapter.content or "", 260) or "本章承接主线剧情推进。")[:800],
                    memory_type="plot_point",
                    chapter_number=chapter.chapter_number,
                    tags=["导入章节", "自动摘要"],
                    importance_score=0.45,
                )
            )

        world_notes: list[str] = []
        for outline in outlines[:30]:
            structure = outline.structure if isinstance(outline.structure, dict) else {}
            scenes = structure.get("scenes") if isinstance(structure.get("scenes"), list) else []
            for scene in scenes[:2]:
                text = str(scene).strip()
                if text and text not in world_notes:
                    world_notes.append(text[:200])

        return BookImportAnalysisDossier(world_notes=world_notes[:40], memories=memories)

    def _merge_text(self, a: Optional[str], b: Optional[str], limit: int) -> Optional[str]:
        first = (a or "").strip()
        second = (b or "").strip()
        if not first:
            return second[:limit] or None
        if not second or second in first:
            return first[:limit]
        return f"{first}\n{second}"[:limit]

    def _is_valid_entity_name(self, name: str) -> bool:
        normalized = (name or "").strip()
        if len(normalized) < 1 or len(normalized) > 100:
            return False
        if normalized in {"主角", "男主", "女主", "某人", "众人", "大家", "他", "她", "我", "我们"}:
            return False
        if re.search(r"[，。！？；：,.!?;:\n\r]", normalized):
            return False
        return True

    def _build_reverse_outline_chapters_text(self, chapters: list[BookImportChapter]) -> str:
        parts: list[str] = []
        for chapter in chapters:
            summary = (chapter.summary or "").strip()
            excerpt = (chapter.content or "").strip()[:2200]
            parts.append(
                f"【第{chapter.chapter_number}章 {chapter.title}】\n"
                f"章节摘要：{summary or '无'}\n"
                f"正文节选：\n{excerpt or '无'}"
            )
        return "\n\n".join(parts)

    def _normalize_reverse_outline_batch(
        self,
        ai_data: Any,
        chapters: list[BookImportChapter],
    ) -> list[dict[str, Any]]:
        ai_items = ai_data if isinstance(ai_data, list) else []
        normalized: list[dict[str, Any]] = []

        for idx, chapter in enumerate(chapters):
            fallback = self._build_fallback_outline_structure(chapter)
            candidate = ai_items[idx] if idx < len(ai_items) and isinstance(ai_items[idx], dict) else {}
            normalized.append(
                self._normalize_single_reverse_outline(
                    candidate,
                    fallback=fallback,
                    chapter_number=chapter.chapter_number,
                    chapter_title=chapter.title,
                )
            )

        return normalized

    def _normalize_single_reverse_outline(
        self,
        raw: dict[str, Any],
        *,
        fallback: dict[str, Any],
        chapter_number: int,
        chapter_title: str,
    ) -> dict[str, Any]:
        summary = str(raw.get("summary") or raw.get("content") or fallback.get("summary") or "").strip()
        if not summary:
            summary = str(fallback.get("summary") or "")

        scenes_raw = raw.get("scenes") if isinstance(raw.get("scenes"), list) else []
        scenes = [str(item).strip() for item in scenes_raw if str(item).strip()][:6]
        if not scenes:
            scenes = list(fallback.get("scenes") or [])

        characters_raw = raw.get("characters") if isinstance(raw.get("characters"), list) else []
        characters: list[dict[str, str]] = []
        for item in characters_raw:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            role_type = "organization" if str(item.get("type") or "").strip() == "organization" else "character"
            characters.append({"name": name[:80], "type": role_type})
        if not characters:
            characters = list(fallback.get("characters") or [])

        key_points_raw = raw.get("key_points") if isinstance(raw.get("key_points"), list) else []
        key_points = [str(item).strip() for item in key_points_raw if str(item).strip()][:8]
        if not key_points:
            key_points = list(fallback.get("key_points") or [])

        emotion = str(raw.get("emotion") or fallback.get("emotion") or "剧情递进").strip() or "剧情递进"
        goal = str(raw.get("goal") or fallback.get("goal") or "推进主线冲突").strip() or "推进主线冲突"

        return {
            "chapter_number": chapter_number,
            "title": chapter_title,
            "summary": summary[:2000],
            "scenes": scenes,
            "characters": characters,
            "key_points": key_points,
            "emotion": emotion[:200],
            "goal": goal[:300],
        }

    def _build_fallback_outline_structure(self, chapter: BookImportChapter) -> dict[str, Any]:
        summary = (chapter.summary or self._build_summary(chapter.content or "")).strip()
        if not summary:
            summary = "本章围绕主要人物与核心冲突推进剧情。"

        return {
            "chapter_number": chapter.chapter_number,
            "title": chapter.title,
            "summary": summary[:1200],
            "scenes": [
                "主角在当前处境中做出关键选择",
                "冲突升级并形成新的悬念",
            ],
            "characters": [],
            "key_points": [
                "推进主线冲突",
                "呈现角色动机与关系变化",
            ],
            "emotion": "紧张递进",
            "goal": "承接前章并推动后续剧情发展",
        }

    def _build_fallback_project_suggestion(
        self,
        *,
        title: str,
        chapters: list[BookImportChapter],
    ) -> ProjectSuggestion:
        sampled_chapters = chapters[:3]
        sampled_text = "\n\n".join((chapter.content or "")[:2000] for chapter in sampled_chapters).strip()
        fallback_description_source = "\n".join(
            [chapter.summary or (chapter.content or "")[:600] for chapter in sampled_chapters]
        ).strip()
        fallback_description = (
            self._build_summary(fallback_description_source)
            or "由拆书功能基于前3章自动提炼：该故事围绕核心人物与主要冲突展开，可在导入前继续修改。"
        )

        return ProjectSuggestion(
            title=title,
            description=fallback_description[:500],
            theme=self._detect_theme_from_text(sampled_text),
            genre=self._detect_genre_from_text(sampled_text),
            narrative_perspective=self._detect_narrative_perspective(sampled_text),
            target_words=100000,
        )

    def _detect_theme_from_text(self, text: str) -> str:
        if any(k in text for k in ("复仇", "报仇", "雪恨")):
            return "复仇与救赎"
        if any(k in text for k in ("成长", "蜕变", "逆袭")):
            return "成长与逆袭"
        if any(k in text for k in ("真相", "谜团", "秘密", "调查")):
            return "真相与抉择"
        if any(k in text for k in ("权谋", "争权", "朝堂", "家族")):
            return "权力与人性"
        if any(k in text for k in ("爱情", "喜欢", "恋爱", "婚约")):
            return "爱情与选择"
        return "命运与选择"

    def _detect_genre_from_text(self, text: str) -> str:
        if any(k in text for k in ("修仙", "宗门", "灵气", "飞升", "仙门")):
            return "仙侠"
        if any(k in text for k in ("玄幻", "异界", "魔法", "斗气")):
            return "玄幻"
        if any(k in text for k in ("星际", "机甲", "赛博", "人工智能", "宇宙")):
            return "科幻"
        if any(k in text for k in ("悬疑", "凶案", "推理", "谜案", "诡")):
            return "悬疑"
        if any(k in text for k in ("总裁", "职场", "都市", "豪门")):
            return "都市"
        if any(k in text for k in ("恋爱", "言情", "心动", "告白")):
            return "言情"
        return "通用"

    def _detect_narrative_perspective(self, text: str) -> str:
        snippet = (text or "")[:6000]
        first_person_hits = len(re.findall(r"[我咱俺]\S{0,2}", snippet))
        third_person_hits = len(re.findall(r"[他她它]\S{0,2}", snippet))

        if first_person_hits >= 20 and first_person_hits > third_person_hits * 1.2:
            return "第一人称"
        return "第三人称"

    def _extract_narrative_perspective(self, project_data: Dict[str, Any], fallback: str = "第三人称") -> str:
        """从AI返回中兼容提取叙事视角字段，统一映射到项目参数可接受值。"""
        if not isinstance(project_data, dict):
            return self._normalize_narrative_perspective(None, fallback)

        candidates = [
            project_data.get("narrative_perspective"),
            project_data.get("narrativePerspective"),
            project_data.get("perspective"),
            project_data.get("narrative_view"),
            project_data.get("narrative_angle"),
            project_data.get("叙事视角"),
            project_data.get("叙事角度"),
            project_data.get("视角"),
        ]

        for value in candidates:
            normalized = self._normalize_narrative_perspective(value, "")
            if normalized:
                return normalized

        return self._normalize_narrative_perspective(None, fallback)

    def _normalize_narrative_perspective(self, value: Any, fallback: str = "第三人称") -> str:
        raw = str(value or "").strip()
        if not raw:
            return fallback

        if raw in {"第一人称", "第三人称", "全知视角"}:
            return raw

        raw_lower = raw.lower().replace("-", "_").replace(" ", "_")
        if raw_lower in {"first_person", "firstperson", "first_person_perspective", "1st_person", "first"}:
            return "第一人称"
        if raw_lower in {"third_person", "thirdperson", "third_person_perspective", "3rd_person", "third"}:
            return "第三人称"
        if raw_lower in {"omniscient", "god_view", "godview", "all_knowing"}:
            return "全知视角"

        if "第一人称" in raw or raw in {"第一视角", "主角视角", "第一人称（我）", "我视角"}:
            return "第一人称"
        if "第三人称" in raw or raw in {"第三视角", "第三人称（他/她）", "旁观视角"}:
            return "第三人称"
        if "全知" in raw or "上帝视角" in raw:
            return "全知视角"

        return fallback

    def _normalize_target_words(self, value: Any, fallback: int = 100000) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = fallback

        if parsed < 1000:
            return fallback
        if parsed > 3000000:
            return 3000000
        return parsed

    async def _build_user_ai_service(self, *, db: AsyncSession, user_id: str) -> AIService:
        """读取用户AI配置并创建支持MCP的AI服务实例。"""
        settings_result = await db.execute(select(Settings).where(Settings.user_id == user_id))
        user_settings = settings_result.scalar_one_or_none()

        if not user_settings:
            default_provider = app_settings.default_ai_provider
            if default_provider == "anthropic":
                default_key = app_settings.anthropic_api_key or ""
                default_base_url = app_settings.anthropic_base_url or ""
            elif default_provider == "gemini":
                default_key = app_settings.gemini_api_key or ""
                default_base_url = app_settings.gemini_base_url or ""
            else:
                default_key = app_settings.openai_api_key or ""
                default_base_url = app_settings.openai_base_url or ""

            user_settings = Settings(
                user_id=user_id,
                api_provider=default_provider,
                api_key=default_key,
                api_base_url=default_base_url,
                llm_model=app_settings.default_model,
                temperature=app_settings.default_temperature,
                max_tokens=app_settings.default_max_tokens,
            )
            db.add(user_settings)
            await db.flush()

        mcp_result = await db.execute(select(MCPPlugin).where(MCPPlugin.user_id == user_id))
        mcp_plugins = mcp_result.scalars().all()
        enable_mcp = any(plugin.enabled for plugin in mcp_plugins) if mcp_plugins else False

        if not user_settings.api_key:
            raise HTTPException(status_code=400, detail="未配置AI Key，无法执行拆书反向生成")

        return create_user_ai_service_with_mcp(
            api_provider=user_settings.api_provider,
            api_key=user_settings.api_key,
            api_base_url=user_settings.api_base_url or "",
            model_name=user_settings.llm_model,
            temperature=user_settings.temperature,
            max_tokens=user_settings.max_tokens,
            user_id=user_id,
            db_session=db,
            system_prompt=user_settings.system_prompt,
            enable_mcp=enable_mcp,
        )

    async def _load_project_followup_counts(self, *, db: AsyncSession, project_id: str) -> dict[str, int]:
        """统计导入后自动补全会产出的项目资产，用于增量补缺。"""

        async def _count(stmt: Any) -> int:
            result = await db.execute(stmt)
            return int(result.scalar_one() or 0)

        return {
            "chapters": await _count(select(func.count()).select_from(Chapter).where(Chapter.project_id == project_id)),
            "outlines": await _count(select(func.count()).select_from(Outline).where(Outline.project_id == project_id)),
            "careers": await _count(select(func.count()).select_from(Career).where(Career.project_id == project_id)),
            "characters": await _count(
                select(func.count()).select_from(Character).where(
                    Character.project_id == project_id,
                    Character.is_organization == False,
                )
            ),
            "organization_characters": await _count(
                select(func.count()).select_from(Character).where(
                    Character.project_id == project_id,
                    Character.is_organization == True,
                )
            ),
            "organizations": await _count(select(func.count()).select_from(Organization).where(Organization.project_id == project_id)),
            "relationships": await _count(
                select(func.count()).select_from(CharacterRelationship).where(CharacterRelationship.project_id == project_id)
            ),
            "organization_members": await _count(
                select(func.count())
                .select_from(OrganizationMember)
                .join(Organization, OrganizationMember.organization_id == Organization.id)
                .where(Organization.project_id == project_id)
            ),
            "memories": await _count(select(func.count()).select_from(StoryMemory).where(StoryMemory.project_id == project_id)),
            "foreshadows": await _count(select(func.count()).select_from(Foreshadow).where(Foreshadow.project_id == project_id)),
        }

    def _project_to_import_suggestion(self, project: Project) -> ProjectSuggestion:
        """将已有项目转成拆书提示上下文，供正文证据型补全复用。"""
        return ProjectSuggestion(
            title=project.title or "拆书导入项目",
            description=project.description,
            theme=project.theme,
            genre=project.genre,
            narrative_perspective=project.narrative_perspective or "第三人称",
            target_words=max(1000, int(project.target_words or 100000)),
        )

    async def _load_project_chapters_for_followup(
        self,
        *,
        db: AsyncSession,
        project_id: str,
        max_chapters: int = 48,
    ) -> tuple[list[BookImportChapter], list[BookImportOutline], dict[int, str]]:
        """
        为导入后补全加载正文样本。

        只使用已落库正文，优先取开篇核心章节，再补一部分尾章，避免在补全阶段
        重新用通用创作提示词发散生成。
        """
        result = await db.execute(
            select(Chapter)
            .where(
                Chapter.project_id == project_id,
                Chapter.content.isnot(None),
            )
            .order_by(Chapter.chapter_number, Chapter.created_at, Chapter.id)
        )
        all_chapters = [
            chapter
            for chapter in result.scalars().all()
            if (chapter.content or "").strip()
        ]
        chapter_id_map: dict[int, str] = {}
        for chapter in all_chapters:
            chapter_id_map.setdefault(chapter.chapter_number, chapter.id)

        if not all_chapters:
            return [], [], chapter_id_map

        if max_chapters > 0 and len(all_chapters) > max_chapters:
            head_count = min(max_chapters, max(12, int(max_chapters * 0.75)))
            tail_count = max(0, max_chapters - head_count)
            selected_by_id: dict[str, Chapter] = {chapter.id: chapter for chapter in all_chapters[:head_count]}
            if tail_count:
                selected_by_id.update({chapter.id: chapter for chapter in all_chapters[-tail_count:]})
            selected_chapters = sorted(
                selected_by_id.values(),
                key=lambda item: (item.chapter_number, item.id),
            )
        else:
            selected_chapters = all_chapters

        chapter_numbers = [chapter.chapter_number for chapter in selected_chapters]
        outline_by_number: dict[int, Outline] = {}
        if chapter_numbers:
            outline_result = await db.execute(
                select(Outline)
                .where(
                    Outline.project_id == project_id,
                    Outline.order_index.in_(chapter_numbers),
                )
                .order_by(Outline.order_index, Outline.created_at, Outline.id)
            )
            for outline in outline_result.scalars().all():
                outline_by_number.setdefault(outline.order_index, outline)

        import_chapters: list[BookImportChapter] = []
        import_outlines: list[BookImportOutline] = []
        for chapter in selected_chapters:
            outline = outline_by_number.get(chapter.chapter_number)
            import_chapters.append(
                BookImportChapter(
                    title=chapter.title,
                    content=chapter.content or "",
                    summary=chapter.summary,
                    chapter_number=chapter.chapter_number,
                    outline_title=outline.title if outline else chapter.title,
                )
            )
            import_outlines.append(
                BookImportOutline(
                    title=outline.title if outline else chapter.title,
                    content=outline.content if outline else chapter.summary,
                    order_index=chapter.chapter_number,
                    structure=self._safe_json_loads(outline.structure) if outline and outline.structure else None,
                )
            )

        return import_chapters, import_outlines, chapter_id_map

    def _safe_json_loads(self, value: Any) -> Optional[dict[str, Any]]:
        if isinstance(value, dict):
            return value
        if not isinstance(value, str) or not value.strip():
            return None
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return None
        return parsed if isinstance(parsed, dict) else None

    async def _complete_world_from_imported_chapters(
        self,
        *,
        db: AsyncSession,
        project: Project,
    ) -> int:
        chapters, _, _ = await self._load_project_chapters_for_followup(
            db=db,
            project_id=project.id,
            max_chapters=6,
        )
        if not chapters:
            return 0

        time_period, location, atmosphere, rules = self._derive_world_settings(
            suggestion=self._project_to_import_suggestion(project),
            chapters=chapters,
        )
        updated = 0
        if not project.world_time_period and time_period:
            project.world_time_period = time_period
            updated = 1
        if not project.world_location and location:
            project.world_location = location
            updated = 1
        if not project.world_atmosphere and atmosphere:
            project.world_atmosphere = atmosphere
            updated = 1
        if not project.world_rules and rules:
            project.world_rules = rules
            updated = 1
        return updated

    def _build_career_evidence_text(self, chapters: list[BookImportChapter]) -> str:
        parts: list[str] = []
        for chapter in chapters:
            content = (chapter.content or "").strip()
            if len(content) > 2600:
                content = f"{content[:1800]}\n……\n{content[-700:]}"
            parts.append(
                f"【第{chapter.chapter_number}章 {chapter.title}】\n"
                f"摘要：{chapter.summary or '无'}\n"
                f"正文：{content or '无'}"
            )
        return "\n\n".join(parts)

    async def _generate_career_system_from_imported_chapters(
        self,
        *,
        db: AsyncSession,
        user_id: str,
        project: Project,
    ) -> int:
        """只根据导入正文中明确出现的修炼、职业、能力体系创建职业数据。"""
        chapters, _, _ = await self._load_project_chapters_for_followup(
            db=db,
            project_id=project.id,
            max_chapters=36,
        )
        if not chapters:
            return 0

        evidence_text = self._build_career_evidence_text(chapters)
        prompt = f"""你是小说拆书智能体中的“体系抽取员”。请只根据输入正文抽取已经出现或被明确提到的职业、修炼、能力、功法或身份体系。

【项目】
书名：{project.title or '拆书导入项目'}
类型：{project.genre or '未设定'}
主题：{project.theme or '未设定'}

【正文证据】
{evidence_text}

【规则】
1. 只能抽取正文中有证据的体系，不能按玄幻/仙侠模板补写。
2. 如果正文没有明确体系，返回空数组，不要为了凑数创造职业。
3. name、stages、requirements、special_abilities、worldview_rules 都必须能从正文证据推断。
4. stages 只列正文明确出现的等级、境界、功法阶段或能力层次。
5. 只输出纯 JSON 对象，不要 markdown 或解释。

【输出格式】
{{
  "main_careers": [
    {{
      "name": "体系名称",
      "description": "80字以内，说明正文证据和作用",
      "category": "修炼/武学/职业/血脉/异能/身份等",
      "stages": [
        {{"level": 1, "name": "正文出现的阶段名", "description": "阶段证据，50字以内"}}
      ],
      "max_stage": 1,
      "requirements": "正文提到的门槛或限制，没有则空",
      "special_abilities": "正文提到的能力表现，没有则空",
      "worldview_rules": "对应世界规则或证据章节"
    }}
  ],
  "sub_careers": []
}}"""

        career_data: dict[str, Any] = {}
        try:
            ai_service = await self._build_user_ai_service(db=db, user_id=user_id)
            raw = await self._call_ai_json_with_resilient_retry(
                ai_service,
                prompt=prompt,
                expected_type="object",
                label="导入正文职业体系抽取",
                max_attempts=4,
            )
            career_data = raw if isinstance(raw, dict) else {}
        except Exception as exc:
            logger.warning(f"导入正文职业体系抽取失败，使用规则证据兜底: {exc}")

        created = await self._replace_project_careers_from_data(
            db=db,
            project_id=project.id,
            career_data=career_data,
            source="imported",
        )
        if created:
            return created

        return await self._derive_careers_from_imported_evidence(
            db=db,
            project_id=project.id,
            evidence_text=evidence_text,
        )

    async def _replace_project_careers_from_data(
        self,
        *,
        db: AsyncSession,
        project_id: str,
        career_data: dict[str, Any],
        source: str,
    ) -> int:
        main_careers = career_data.get("main_careers", [])
        sub_careers = career_data.get("sub_careers", [])
        if not isinstance(main_careers, list):
            main_careers = []
        if not isinstance(sub_careers, list):
            sub_careers = []

        career_ids_result = await db.execute(select(Career.id).where(Career.project_id == project_id))
        career_ids = [row[0] for row in career_ids_result.fetchall()]
        if career_ids:
            await db.execute(delete(CharacterCareer).where(CharacterCareer.career_id.in_(career_ids)))
            await db.execute(delete(Career).where(Career.project_id == project_id))

        def _to_int(value: Any, default: int) -> int:
            try:
                parsed = int(value)
            except (TypeError, ValueError):
                parsed = default
            return max(1, parsed)

        def _normalize_stages(value: Any) -> list[dict[str, Any]]:
            if not isinstance(value, list):
                return []
            stages: list[dict[str, Any]] = []
            for idx, stage in enumerate(value[:20], start=1):
                if isinstance(stage, dict):
                    name = str(stage.get("name") or "").strip()
                    description = str(stage.get("description") or "").strip()
                    level = _to_int(stage.get("level"), idx)
                else:
                    name = str(stage or "").strip()
                    description = ""
                    level = idx
                if not name:
                    continue
                stages.append({"level": level, "name": name[:80], "description": description[:300]})
            return stages

        created = 0
        seen_names: set[str] = set()

        def _add_items(items: list[Any], career_type: str) -> None:
            nonlocal created
            for item in items[:8]:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or "").strip()
                if not name or name in seen_names:
                    continue
                stages = _normalize_stages(item.get("stages"))
                if not stages:
                    continue
                max_stage = max(_to_int(item.get("max_stage"), len(stages)), len(stages))
                db.add(
                    Career(
                        project_id=project_id,
                        name=name[:100],
                        type=career_type,
                        description=str(item.get("description") or "").strip()[:1000] or None,
                        category=str(item.get("category") or "").strip()[:50] or None,
                        stages=json.dumps(stages, ensure_ascii=False),
                        max_stage=max_stage,
                        requirements=str(item.get("requirements") or "").strip()[:1000] or None,
                        special_abilities=str(item.get("special_abilities") or "").strip()[:1000] or None,
                        worldview_rules=str(item.get("worldview_rules") or "").strip()[:1000] or None,
                        attribute_bonuses=json.dumps(item.get("attribute_bonuses"), ensure_ascii=False)
                        if item.get("attribute_bonuses")
                        else None,
                        source=source[:20],
                    )
                )
                seen_names.add(name)
                created += 1

        _add_items(main_careers, "main")
        _add_items(sub_careers, "sub")
        await db.flush()
        return created

    async def _derive_careers_from_imported_evidence(
        self,
        *,
        db: AsyncSession,
        project_id: str,
        evidence_text: str,
    ) -> int:
        """AI 没给出可用结果时，只按正文关键词建立最小可用体系。"""
        text = evidence_text or ""
        stage_terms = [
            "炼体", "筑基", "培元", "真气", "先天", "通玄", "凝元", "金丹",
            "元婴", "武者", "武师", "武宗", "武王", "武皇", "武圣", "武神",
        ]
        found_stages = [term for term in stage_terms if term in text]
        if not found_stages:
            return 0

        sub_terms = {
            "丹道": ("炼丹", "丹药", "神丹"),
            "器道": ("炼器", "法器", "兵器"),
            "阵道": ("阵法", "法阵"),
            "拳法": ("拳法", "拳劲"),
            "身法": ("身法", "步法", "杀生步"),
        }
        sub_careers: list[dict[str, Any]] = []
        for name, hints in sub_terms.items():
            matched = [hint for hint in hints if hint in text]
            if not matched:
                continue
            sub_careers.append(
                {
                    "name": name,
                    "description": f"正文已出现相关能力或技艺：{'、'.join(matched[:3])}",
                    "category": "能力技艺",
                    "stages": [{"level": 1, "name": matched[0], "description": "正文出现的能力线索"}],
                    "max_stage": 1,
                    "requirements": "",
                    "special_abilities": "、".join(matched[:3]),
                    "worldview_rules": "由导入正文关键词抽取",
                }
            )

        career_data = {
            "main_careers": [
                {
                    "name": "武道修行",
                    "description": "由导入正文中出现的修炼、境界、功法线索抽取的主体系。",
                    "category": "修炼",
                    "stages": [
                        {"level": idx + 1, "name": term, "description": "正文出现的修炼或境界线索"}
                        for idx, term in enumerate(dict.fromkeys(found_stages[:12]))
                    ],
                    "max_stage": len(dict.fromkeys(found_stages[:12])),
                    "requirements": "以正文出现的修炼、功法和境界描写为准",
                    "special_abilities": "、".join(dict.fromkeys(found_stages[:8])),
                    "worldview_rules": "由导入正文关键词抽取",
                }
            ],
            "sub_careers": sub_careers[:4],
        }
        return await self._replace_project_careers_from_data(
            db=db,
            project_id=project_id,
            career_data=career_data,
            source="imported",
        )

    async def _complete_story_dossier_from_imported_chapters(
        self,
        *,
        db: AsyncSession,
        user_id: str,
        project: Project,
    ) -> dict[str, int]:
        chapters, outlines, chapter_id_map = await self._load_project_chapters_for_followup(
            db=db,
            project_id=project.id,
            max_chapters=24,
        )
        if not chapters:
            return {
                "generated_characters": 0,
                "generated_organizations": 0,
                "generated_relationships": 0,
                "generated_memories": 0,
                "generated_foreshadows": 0,
            }

        dossier = await self._generate_deep_story_dossier(
            user_id=user_id,
            suggestion=self._project_to_import_suggestion(project),
            chapters=chapters,
            outlines=outlines,
            task=None,
        )
        stats = await self._import_story_dossier(
            db=db,
            project=project,
            dossier=dossier,
            chapter_id_map=chapter_id_map,
        )
        await db.flush()
        if stats.get("generated_characters", 0) or stats.get("generated_organizations", 0):
            return stats

        heuristic_dossier = self._build_evidence_fallback_entity_dossier(chapters=chapters)
        if heuristic_dossier.characters or heuristic_dossier.organizations:
            fallback_stats = await self._import_story_dossier(
                db=db,
                project=project,
                dossier=heuristic_dossier,
                chapter_id_map=chapter_id_map,
            )
            await db.flush()
            for key, value in fallback_stats.items():
                stats[key] = int(stats.get(key, 0) or 0) + int(value or 0)

        return stats

    def _build_evidence_fallback_entity_dossier(
        self,
        *,
        chapters: list[BookImportChapter],
    ) -> BookImportAnalysisDossier:
        """
        AI 档案抽取失败或过慢时的正文证据兜底。

        只抽取“姓名/组织名 + 动作、称谓、发言或身份说明”同时出现的高置信实体，
        不按题材模板补写人物。
        """
        if not chapters:
            return BookImportAnalysisDossier()

        stop_names = {
            "主人", "老夫", "对方", "少年", "少女", "青年", "下人", "父母", "身体", "肉身",
            "经脉", "资源", "力量", "声音", "宝衣", "神土", "魔神", "武者", "武徒", "武师",
            "大武师", "大人物", "神情", "众人", "几人", "此人", "那人", "小子", "恶人",
            "天元大陆", "山水镇", "枫叶城", "朱雀", "寒冬", "大江", "草房",
            "冷冷", "淡淡", "自语", "当即", "勿惊", "得一声", "小畜生", "你不会", "又岂会",
            "往往会", "只有", "主人勿惊", "咔嚓", "嘿嘿", "放肆", "沉声", "立刻", "要知",
            "连忙", "只见", "只见一", "此时", "我们", "咱们", "你们", "他们", "于是他",
            "后者", "大声", "怪笑", "更不要", "点头", "皱眉", "亲手打造", "亲至", "坐镇",
            "成为核心",
        }
        false_name_prefixes = (
            "只", "要", "连", "放", "沉", "立", "咔", "嘿", "我", "咱", "你", "他", "她",
            "它", "这", "那", "又", "往", "的", "返", "离", "可", "不", "一", "二", "三",
            "四", "五", "六", "七", "八", "九", "十", "亲", "坐", "成",
        )
        false_name_fragments = (
            "此时", "只见", "一声", "不会", "岂会", "往往", "得一", "放肆", "于是",
            "亲手", "成为", "的声", "分析",
        )
        title_suffixes = ("兄弟", "大哥", "大人", "先生", "姑娘", "小姐", "前辈", "长老", "族长")
        surname_prefixes = tuple(
            "赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜"
            "戚谢邹喻柏水窦章云苏潘葛奚范彭郎鲁韦昌马苗凤花方俞任袁柳鲍"
            "史唐费廉岑薛雷贺倪汤滕殷罗毕郝邬安常乐时傅皮卞齐康伍余"
            "元卜顾孟平黄和穆萧尹叶胡林高梁宋杜程卢蔡田邓郭曾肖北明"
        )
        organization_suffixes = ("家", "神土", "皇朝", "宗", "门", "阁", "帮", "盟", "府", "宫", "殿", "院", "族", "教", "圣地")
        action_pattern = re.compile(
            r"([一-龥]{2,4}(?:儿)?)(?:"
            r"道|说道|问道|问|说|回答|答道|喝道|叫道|笑道|冷笑|大笑|怒道|"
            r"点头|摇头|皱眉|看着|听到|想到|心中|返回|换好|站起|坐下|"
            r"立刻|当即|继续|告诉|喃喃|狂叫|大叫|打量|吓得|震惊|狂喜"
            r")"
        )
        explicit_patterns = [
            re.compile(r"(?:名叫|唤作|唤我|叫做|名为)(?:“|‘)?([一-龥]{2,4}(?:儿)?)(?:”|’)?"),
            re.compile(r"(?:族长之孙|族长之子|族长|具灵|老祖|管事|将军)([一-龥]{2,4}(?:儿)?)"),
        ]
        quote_name_pattern = re.compile(r"(?:“|”|’|')([一-龥]{2,4}(?:儿)?)(?:“|”|’|')?")

        def _normalize_candidate_name(name: str) -> str:
            normalized = name.strip("“”‘’'\" ，。！？：:；;")
            if "的" in normalized and len(normalized) > 2:
                normalized = normalized.split("的", 1)[0]
            for suffix in ("冷冷", "淡淡", "当即", "自语", "此时", "连忙", "沉声", "分析"):
                if len(normalized) > len(suffix) + 1 and normalized.endswith(suffix):
                    normalized = normalized[: -len(suffix)]
            for suffix in ("次", "心"):
                if len(normalized) > 2 and normalized.endswith(suffix):
                    normalized = normalized[:-1]
            if len(normalized) > 2 and normalized.endswith("点"):
                normalized = normalized[:-1]
            return normalized

        def _valid_name(name: str) -> bool:
            if not self._is_valid_entity_name(name):
                return False
            if name in stop_names:
                return False
            if name.startswith(false_name_prefixes):
                return False
            if any(word in name for word in ("什么", "为何", "不是", "可以", "知道", "主人", "一声")):
                return False
            if any(fragment in name for fragment in false_name_fragments):
                return False
            if name.endswith(title_suffixes):
                return False
            if name.endswith(organization_suffixes):
                return False
            return True

        def _valid_action_name(name: str) -> bool:
            normalized = _normalize_candidate_name(name)
            if not _valid_name(normalized):
                return False
            return normalized.startswith(surname_prefixes) or normalized.endswith("儿")

        evidence_by_name: dict[str, dict[str, Any]] = {}

        def _add_name(name: str, chapter: BookImportChapter, score: int, evidence: str) -> None:
            normalized = _normalize_candidate_name(name)
            if not _valid_name(normalized):
                return
            item = evidence_by_name.setdefault(
                normalized,
                {
                    "score": 0,
                    "first_seen": chapter.chapter_number,
                    "chapters": set(),
                    "evidence": [],
                },
            )
            item["score"] += score
            item["first_seen"] = min(item["first_seen"], chapter.chapter_number)
            item["chapters"].add(chapter.chapter_number)
            if evidence and len(item["evidence"]) < 3:
                item["evidence"].append(evidence[:160])

        for chapter in chapters[:24]:
            content = (chapter.content or "")[:12000]
            for pattern in explicit_patterns:
                for match in pattern.finditer(content):
                    start = max(0, match.start() - 80)
                    end = min(len(content), match.end() + 100)
                    _add_name(match.group(1), chapter, 5, content[start:end].replace("\n", " "))

            for match in action_pattern.finditer(content):
                start = max(0, match.start() - 80)
                end = min(len(content), match.end() + 100)
                if _valid_action_name(match.group(1)):
                    _add_name(match.group(1), chapter, 2, content[start:end].replace("\n", " "))

            for quoted in quote_name_pattern.finditer(content):
                name = quoted.group(1)
                if len(name) <= 4 and quoted.start() > 0 and quoted.end() < len(content):
                    window = content[max(0, quoted.start() - 40): min(len(content), quoted.end() + 40)]
                    if "唤我" in window or "叫" in window or "名" in window:
                        _add_name(name, chapter, 4, window.replace("\n", " "))

        candidate_name_items = [
            (name, data)
            for name, data in evidence_by_name.items()
            if data["score"] >= 4 or len(data["chapters"]) >= 2
        ]
        candidate_names = {name for name, _ in candidate_name_items}
        ranked_names = sorted(
            [
                (name, data)
                for name, data in candidate_name_items
                if not any(other != name and len(other) > len(name) and other.endswith(name) for other in candidate_names)
            ],
            key=lambda item: (-int(item[1]["score"]), int(item[1]["first_seen"]), item[0]),
        )[:18]

        characters: list[BookImportExtractedCharacter] = []
        for idx, (name, data) in enumerate(ranked_names):
            evidence = "；".join(data["evidence"]) or f"第{data['first_seen']}章出现。"
            role_type = "protagonist" if idx == 0 else "supporting"
            characters.append(
                BookImportExtractedCharacter(
                    name=name,
                    role_type=role_type,
                    personality="由导入正文中的行动、发言或身份信息抽取，待后续章节分析继续细化。",
                    background=evidence[:1000],
                    current_state=f"首次见于第{data['first_seen']}章，已在原文中出现明确姓名或称谓证据。",
                    traits=["原文出现", "规则抽取"],
                    first_seen_chapter=int(data["first_seen"]),
                    importance=max(0.45, min(0.95, float(data["score"]) / 18.0)),
                )
            )

        org_patterns = [
            re.compile(r"([一-龥]{1,3}家)"),
            re.compile(r"([一-龥]{2,6}(?:神土|皇朝|圣地|宗|门|阁|帮|盟|府|宫|殿|院|族|教))"),
        ]
        org_stop = {
            "大家", "这家", "那家", "一家", "回家", "管事", "大宅", "外门", "内门",
            "五大皇朝", "青铜世家", "三大世家", "四大世家", "五大世家", "世家", "了家", "国家", "开家",
        }
        org_false_prefixes = {"你", "又", "往", "的", "离", "返", "只", "可", "不", "我", "咱", "他", "她", "了", "国", "世", "所", "是", "开"}
        number_prefixes = {"一", "二", "三", "四", "五", "六", "七", "八", "九", "十", "几", "诸", "各"}

        def _normalize_org_name(name: str) -> str:
            normalized = name.strip("“”‘’'\" ，。！？：:；;")
            for prefix in ("我们", "咱们", "你们", "他们", "返回", "离开", "来到", "进入", "所以", "是"):
                if normalized.startswith(prefix) and len(normalized) > len(prefix) + 1:
                    normalized = normalized[len(prefix):]
            if normalized.endswith("家") and len(normalized) > 3:
                normalized = normalized[-2:]
            return normalized

        def _valid_org_name(name: str) -> bool:
            if not self._is_valid_entity_name(name):
                return False
            if name in org_stop or len(name) < 2:
                return False
            if name[0] in org_false_prefixes or name[0] in number_prefixes:
                return False
            if name.endswith("世家") and len(name) > 3:
                return False
            return True

        org_data: dict[str, dict[str, Any]] = {}
        for chapter in chapters[:24]:
            content = (chapter.content or "")[:12000]
            for org_pattern in org_patterns:
                for match in org_pattern.finditer(content):
                    name = _normalize_org_name(match.group(1))
                    if not _valid_org_name(name):
                        continue
                    item = org_data.setdefault(name, {"count": 0, "first_seen": chapter.chapter_number})
                    item["count"] += 1
                    item["first_seen"] = min(item["first_seen"], chapter.chapter_number)

        organizations: list[BookImportExtractedOrganization] = []
        for name, data in sorted(org_data.items(), key=lambda item: (-item[1]["count"], item[1]["first_seen"], item[0]))[:10]:
            if data["count"] < 2 and name not in {"叶家", "通天神土"}:
                continue
            organizations.append(
                BookImportExtractedOrganization(
                    name=name[:100],
                    organization_type="正文势力",
                    purpose="由导入正文中的组织/势力名抽取，具体目标待后续分析细化。",
                    background=f"第{data['first_seen']}章起在正文中出现，共匹配到{data['count']}次。",
                    power_level=max(30, min(90, 30 + int(data["count"]) * 5)),
                    traits=["原文出现", "规则抽取"],
                )
            )

        relationships: list[BookImportExtractedRelationship] = []
        if len(characters) >= 2:
            protagonist = characters[0]
            for target in characters[1:9]:
                shared_chapter = target.first_seen_chapter or protagonist.first_seen_chapter or 1
                relationships.append(
                    BookImportExtractedRelationship(
                        source=protagonist.name,
                        target=target.name,
                        relationship_type="同章关联",
                        intimacy_level=40,
                        status="active",
                        description=f"规则兜底：{protagonist.name}与{target.name}均在第{shared_chapter}章前后出现，关系需由后续章节分析继续细化。",
                    )
                )

        return BookImportAnalysisDossier(
            characters=characters,
            relationships=relationships,
            organizations=organizations,
        )

    async def _run_post_import_wizard_generation(
        self,
        *,
        db: AsyncSession,
        user_id: str,
        project: Project,
        character_count: int,
    ) -> tuple[int, int, int]:
        """
        导入后的内容证据型补全：
        1) 基于已导入正文补足世界观字段
        2) 只从正文证据抽取职业/修炼/能力体系
        3) 只从正文证据抽取角色、组织、关系和组织成员
        不生成大纲。
        """
        counts = await self._load_project_followup_counts(db=db, project_id=project.id)
        has_imported_chapters = counts["chapters"] > 0
        world_completed = bool(
            project.world_time_period
            or project.world_location
            or project.world_atmosphere
            or project.world_rules
        )

        generated_world = 0
        if not world_completed:
            if has_imported_chapters:
                generated_world = await self._complete_world_from_imported_chapters(
                    db=db,
                    project=project,
                )
            else:
                generated_world = await self._generate_world_building_from_project(
                    db=db,
                    user_id=user_id,
                    project=project,
                )
        else:
            logger.info(f"拆书导入：项目 {project.id} 已有世界观，跳过世界观补全")

        generated_careers = 0
        if counts["careers"] <= 0:
            if has_imported_chapters:
                generated_careers = await self._generate_career_system_from_imported_chapters(
                    db=db,
                    user_id=user_id,
                    project=project,
                )
            else:
                generated_careers = await self._generate_career_system_from_project(
                    db=db,
                    user_id=user_id,
                    project=project,
                )
            await db.flush()
            counts = await self._load_project_followup_counts(db=db, project_id=project.id)
        else:
            logger.info(f"拆书导入：项目 {project.id} 已有职业体系，跳过职业补全")

        needs_entities = (
            counts["characters"] <= 0
            or (counts["organizations"] <= 0 and counts["organization_characters"] <= 0)
        )
        generated_entities = 0
        needs_relations = counts["relationships"] <= 0 and counts["characters"] > 1
        needs_members = counts["organization_members"] <= 0 and counts["organizations"] > 0 and counts["characters"] > 0
        if needs_entities or needs_relations or needs_members:
            if has_imported_chapters:
                dossier_stats = await self._complete_story_dossier_from_imported_chapters(
                    db=db,
                    user_id=user_id,
                    project=project,
                )
                generated_entities = (
                    dossier_stats.get("generated_characters", 0)
                    + dossier_stats.get("generated_organizations", 0)
                    + dossier_stats.get("generated_relationships", 0)
                )
            else:
                generated_entities = await self._generate_characters_and_organizations_from_project(
                    db=db,
                    user_id=user_id,
                    project=project,
                    count=character_count,
                )
            await db.flush()
            counts = await self._load_project_followup_counts(db=db, project_id=project.id)
        else:
            logger.info(f"拆书导入：项目 {project.id} 已有角色/组织/关系，跳过实体补全")

        if not has_imported_chapters and counts["relationships"] <= 0 and counts["characters"] > 1:
            created_relationships = await self._generate_relationships_for_existing_characters(
                db=db,
                user_id=user_id,
                project=project,
            )
            generated_entities += created_relationships
            if created_relationships:
                await db.flush()
                counts = await self._load_project_followup_counts(db=db, project_id=project.id)

        if (
            not has_imported_chapters
            and counts["organization_members"] <= 0
            and counts["organizations"] > 0
            and counts["characters"] > 0
        ):
            created_memberships = await self._generate_memberships_for_existing_organizations(
                db=db,
                user_id=user_id,
                project=project,
            )
            generated_entities += created_memberships

        # 拆书导入场景不需要继续到大纲，直接标记流程完成，避免项目列表再次跳向导生成大纲。
        project.wizard_step = 4
        project.wizard_status = "completed"
        project.status = "writing"

        return generated_world, generated_careers, generated_entities

    async def _generate_world_building_from_project(
        self,
        *,
        db: AsyncSession,
        user_id: str,
        project: Project,
        progress_callback: Any = None,
        progress_range: tuple[int, int] = (0, 100),
        raise_on_error: bool = False,
    ) -> int:
        """根据反向生成的项目基础信息，优先生成并写入世界观。"""

        async def _notify(msg: str, sub: float) -> None:
            if progress_callback:
                p = progress_range[0] + int((progress_range[1] - progress_range[0]) * sub)
                await progress_callback(msg, p)

        try:
            await _notify("🌍 正在初始化AI服务...", 0.1)
            ai_service = await self._build_user_ai_service(db=db, user_id=user_id)

            await _notify("🌍 正在准备世界观提示词...", 0.2)
            template = await PromptService.get_template("WORLD_BUILDING", user_id, db)
            prompt = PromptService.format_prompt(
                template,
                title=project.title or "拆书导入项目",
                genre=project.genre or "通用",
                theme=project.theme or "未设定",
                description=project.description or "暂无简介",
            )

            await _notify("🌍 AI正在生成世界观...", 0.3)
            world_data = await self._call_ai_json_with_resilient_retry(
                ai_service,
                prompt=prompt,
                expected_type="object",
                label="世界观生成",
                max_attempts=8,
            )
            if not isinstance(world_data, dict):
                return 0

            await _notify("🌍 正在解析世界观数据...", 0.8)
            time_period = str(world_data.get("time_period") or "").strip()
            location = str(world_data.get("location") or "").strip()
            atmosphere = str(world_data.get("atmosphere") or "").strip()
            rules = str(world_data.get("rules") or "").strip()

            updated = 0
            if time_period:
                project.world_time_period = time_period
                updated = 1
            if location:
                project.world_location = location
                updated = 1
            if atmosphere:
                project.world_atmosphere = atmosphere
                updated = 1
            if rules:
                project.world_rules = rules
                updated = 1

            await _notify("🌍 世界观写入完成", 1.0)
            return updated
        except Exception as exc:
            logger.warning(f"拆书导入阶段生成世界观失败，沿用现有世界观: {exc}")
            if raise_on_error:
                raise
            return 0

    async def _generate_career_system_from_project(
        self,
        *,
        db: AsyncSession,
        user_id: str,
        project: Project,
        progress_callback: Any = None,
        progress_range: tuple[int, int] = (0, 100),
    ) -> int:
        """根据项目世界观生成职业体系（3主2副）。"""

        async def _notify(msg: str, sub: float) -> None:
            if progress_callback:
                p = progress_range[0] + int((progress_range[1] - progress_range[0]) * sub)
                await progress_callback(msg, p)

        await _notify("💼 正在初始化AI服务...", 0.1)
        ai_service = await self._build_user_ai_service(db=db, user_id=user_id)

        await _notify("💼 正在准备职业体系提示词...", 0.2)
        template = await PromptService.get_template("CAREER_SYSTEM_GENERATION", user_id, db)
        prompt = PromptService.format_prompt(
            template,
            title=project.title,
            genre=project.genre or "未设定",
            theme=project.theme or "未设定",
            description=project.description or "暂无简介",
            time_period=project.world_time_period or "未设定",
            location=project.world_location or "未设定",
            atmosphere=project.world_atmosphere or "未设定",
            rules=project.world_rules or "未设定",
        )

        await _notify("💼 AI正在生成职业体系...", 0.3)
        career_data = await self._call_ai_json_with_resilient_retry(
            ai_service,
            prompt=prompt,
            expected_type="object",
            label="职业体系生成",
            max_attempts=8,
        )

        await _notify("💼 正在解析职业数据...", 0.7)
        main_careers = career_data.get("main_careers", [])
        sub_careers = career_data.get("sub_careers", [])
        if not isinstance(main_careers, list):
            main_careers = []
        if not isinstance(sub_careers, list):
            sub_careers = []

        # 清理历史职业，避免重复（拆书导入走新建项目，但这里保持幂等）
        career_ids_result = await db.execute(select(Career.id).where(Career.project_id == project.id))
        career_ids = [row[0] for row in career_ids_result.fetchall()]
        if career_ids:
            await db.execute(delete(CharacterCareer).where(CharacterCareer.career_id.in_(career_ids)))
            await db.execute(delete(Career).where(Career.project_id == project.id))

        created = 0

        def _to_career_model(item: dict[str, Any], career_type: str, idx: int) -> Career:
            stages = item.get("stages", [])
            if not isinstance(stages, list):
                stages = []
            max_stage = item.get("max_stage", len(stages) if stages else (10 if career_type == "main" else 6))
            if not isinstance(max_stage, int) or max_stage <= 0:
                max_stage = len(stages) if stages else (10 if career_type == "main" else 6)

            attr_bonuses = item.get("attribute_bonuses")
            attr_bonuses_json = json.dumps(attr_bonuses, ensure_ascii=False) if attr_bonuses else None

            return Career(
                project_id=project.id,
                name=(item.get("name") or f"未命名{'主' if career_type == 'main' else '副'}职业{idx + 1}")[:100],
                type=career_type,
                description=item.get("description"),
                category=item.get("category"),
                stages=json.dumps(stages, ensure_ascii=False),
                max_stage=max_stage,
                requirements=item.get("requirements"),
                special_abilities=item.get("special_abilities"),
                worldview_rules=item.get("worldview_rules"),
                attribute_bonuses=attr_bonuses_json,
                source="ai",
            )

        for idx, item in enumerate(main_careers):
            if not isinstance(item, dict):
                continue
            db.add(_to_career_model(item, "main", idx))
            created += 1

        for idx, item in enumerate(sub_careers):
            if not isinstance(item, dict):
                continue
            db.add(_to_career_model(item, "sub", idx))
            created += 1

        await db.flush()
        return created

    async def _generate_characters_and_organizations_from_project(
        self,
        *,
        db: AsyncSession,
        user_id: str,
        project: Project,
        count: int,
        progress_callback: Any = None,
        progress_range: tuple[int, int] = (0, 100),
    ) -> int:
        """根据世界观+职业体系生成角色/组织，并补全职业和组织成员关系。"""

        async def _notify(msg: str, sub: float) -> None:
            if progress_callback:
                p = progress_range[0] + int((progress_range[1] - progress_range[0]) * sub)
                await progress_callback(msg, p)

        def _to_int(value: Any, default: int) -> int:
            try:
                return int(value)
            except (TypeError, ValueError):
                return default

        await _notify("👥 正在初始化AI服务...", 0.05)
        ai_service = await self._build_user_ai_service(db=db, user_id=user_id)

        # 控制数量区间，避免过多生成
        target_count = max(5, min(count, 20))

        # 职业上下文：用于提示词约束与后续名称映射
        careers_result = await db.execute(select(Career).where(Career.project_id == project.id))
        careers = careers_result.scalars().all()
        main_careers = [c for c in careers if c.type == "main"]
        sub_careers = [c for c in careers if c.type == "sub"]
        main_career_map = {c.name: c for c in main_careers}
        sub_career_map = {c.name: c for c in sub_careers}

        await _notify("👥 正在准备角色生成提示词...", 0.15)
        template = await PromptService.get_template("CHARACTERS_BATCH_GENERATION", user_id, db)
        requirements = (
            "请生成能够支撑前期剧情推进的关键角色与组织，"
            "角色和组织都要与世界观、职业体系一致。"
            "如果包含组织，数量不超过2个。"
            "请尽量为非组织角色补充 organization_memberships。"
        )

        if main_careers or sub_careers:
            careers_context = "\n\n【职业分配要求】\n"
            careers_context += "请为每个非组织角色返回 career_assignment 字段："
            careers_context += '{"main_career":"主职业名称","main_stage":2,"sub_careers":[{"career":"副职业名称","stage":1}]}'
            careers_context += "\n职业名称必须从以下列表中选择：\n"
            if main_careers:
                careers_context += "- 可用主职业：" + "、".join([c.name for c in main_careers]) + "\n"
            if sub_careers:
                careers_context += "- 可用副职业：" + "、".join([c.name for c in sub_careers]) + "\n"
            requirements += careers_context

        prompt = PromptService.format_prompt(
            template,
            count=target_count,
            time_period=project.world_time_period or "未设定",
            location=project.world_location or "未设定",
            atmosphere=project.world_atmosphere or "未设定",
            rules=project.world_rules or "未设定",
            theme=project.theme or "未设定",
            genre=project.genre or "未设定",
            requirements=requirements,
        )

        await _notify("👥 AI正在生成角色与组织...", 0.25)
        generated_data = await self._call_ai_json_with_resilient_retry(
            ai_service,
            prompt=prompt,
            expected_type="array",
            label="角色与组织生成",
            max_attempts=8,
        )
        await _notify("👥 正在解析角色数据...", 0.7)
        if isinstance(generated_data, dict):
            generated_entities = [generated_data]
        elif isinstance(generated_data, list):
            generated_entities = generated_data
        else:
            generated_entities = []

        # 预加载角色/组织，便于去重和兼容 append 场景的名称引用
        existing_chars_result = await db.execute(select(Character).where(Character.project_id == project.id))
        existing_chars = existing_chars_result.scalars().all()
        existing_names = {c.name for c in existing_chars}
        character_name_to_obj: dict[str, Character] = {c.name: c for c in existing_chars}

        existing_orgs_result = await db.execute(
            select(Organization, Character.name)
            .join(Character, Organization.character_id == Character.id)
            .where(Organization.project_id == project.id)
        )
        organization_name_to_obj: dict[str, Organization] = {
            row[1]: row[0] for row in existing_orgs_result.all() if row[1]
        }

        existing_member_result = await db.execute(
            select(OrganizationMember.organization_id, OrganizationMember.character_id)
            .join(Organization, OrganizationMember.organization_id == Organization.id)
            .where(Organization.project_id == project.id)
        )
        member_pairs = {(row[0], row[1]) for row in existing_member_result.all()}

        existing_rel_result = await db.execute(
            select(CharacterRelationship.character_from_id, CharacterRelationship.character_to_id)
            .where(CharacterRelationship.project_id == project.id)
        )
        relationship_pairs = {(row[0], row[1]) for row in existing_rel_result.all()}

        rel_type_result = await db.execute(select(RelationshipType))
        relationship_type_map: dict[str, int] = {
            rel_type.name: rel_type.id
            for rel_type in rel_type_result.scalars().all()
            if rel_type.name
        }

        created = 0
        created_items: list[tuple[Character, dict[str, Any]]] = []

        # 第一阶段：创建 Character / Organization 实体
        for item in generated_entities:
            if not isinstance(item, dict):
                continue

            raw_name = (item.get("name") or "").strip()
            if not raw_name or raw_name in existing_names:
                continue

            is_organization = bool(item.get("is_organization", False))
            character = Character(
                project_id=project.id,
                name=raw_name[:100],
                age=(str(item.get("age")) if item.get("age") is not None else None) if not is_organization else None,
                gender=item.get("gender") if not is_organization else None,
                is_organization=is_organization,
                role_type=(item.get("role_type") or "supporting")[:50],
                personality=item.get("personality"),
                background=item.get("background"),
                appearance=item.get("appearance"),
                organization_type=item.get("organization_type") if is_organization else None,
                organization_purpose=item.get("organization_purpose") if is_organization else None,
                organization_members=(
                    json.dumps(item.get("organization_members"), ensure_ascii=False)
                    if item.get("organization_members") is not None else None
                ),
                traits=json.dumps(item.get("traits", []), ensure_ascii=False) if item.get("traits") else None,
            )
            db.add(character)
            await db.flush()

            if is_organization:
                organization = Organization(
                    character_id=character.id,
                    project_id=project.id,
                    power_level=max(0, min(_to_int(item.get("power_level", 50), 50), 100)),
                    member_count=0,
                    location=item.get("location"),
                    motto=item.get("motto"),
                    color=item.get("color"),
                )
                db.add(organization)
                await db.flush()
                organization_name_to_obj[character.name] = organization

            created_items.append((character, item))
            character_name_to_obj[character.name] = character
            existing_names.add(raw_name)
            created += 1

        # 第二阶段：创建职业关联（CharacterCareer + 冗余字段）
        if created_items and (main_career_map or sub_career_map):
            career_pairs: set[tuple[str, str]] = set()

            for character, item in created_items:
                if character.is_organization:
                    continue

                # 兼容两种字段：career_assignment(批量) / career_info(单角色)
                assignment = item.get("career_assignment")
                if not isinstance(assignment, dict):
                    career_info = item.get("career_info")
                    if isinstance(career_info, dict):
                        assignment = {
                            "main_career": career_info.get("main_career_name"),
                            "main_stage": career_info.get("main_career_stage", 1),
                            "sub_careers": [
                                {
                                    "career": sub.get("career_name"),
                                    "stage": sub.get("stage", 1),
                                }
                                for sub in (career_info.get("sub_careers") or [])
                                if isinstance(sub, dict)
                            ],
                        }

                if not isinstance(assignment, dict):
                    continue

                # 主职业
                main_name = (assignment.get("main_career") or "").strip()
                if main_name and main_name in main_career_map:
                    main_career = main_career_map[main_name]
                    main_stage = max(1, min(_to_int(assignment.get("main_stage", 1), 1), max(main_career.max_stage or 1, 1)))
                    main_key = (character.id, main_career.id)
                    if main_key not in career_pairs:
                        db.add(
                            CharacterCareer(
                                character_id=character.id,
                                career_id=main_career.id,
                                career_type="main",
                                current_stage=main_stage,
                                stage_progress=0,
                            )
                        )
                        career_pairs.add(main_key)

                    character.main_career_id = main_career.id
                    character.main_career_stage = main_stage

                # 副职业
                sub_list = assignment.get("sub_careers") or []
                if not isinstance(sub_list, list):
                    sub_list = []

                sub_career_json: list[dict[str, Any]] = []
                for sub in sub_list[:2]:
                    if not isinstance(sub, dict):
                        continue
                    sub_name = (sub.get("career") or "").strip()
                    if not sub_name or sub_name not in sub_career_map:
                        continue

                    sub_career = sub_career_map[sub_name]
                    sub_stage = max(1, min(_to_int(sub.get("stage", 1), 1), max(sub_career.max_stage or 1, 1)))
                    sub_key = (character.id, sub_career.id)
                    if sub_key in career_pairs:
                        continue

                    db.add(
                        CharacterCareer(
                            character_id=character.id,
                            career_id=sub_career.id,
                            career_type="sub",
                            current_stage=sub_stage,
                            stage_progress=0,
                        )
                    )
                    career_pairs.add(sub_key)
                    sub_career_json.append({"career_id": sub_career.id, "stage": sub_stage})

                if sub_career_json:
                    character.sub_careers = json.dumps(sub_career_json, ensure_ascii=False)

        # 第三阶段：创建角色关系（relationships_array / relationships）
        for character, item in created_items:
            if character.is_organization:
                continue

            relationships_data = item.get("relationships_array")
            if not isinstance(relationships_data, list):
                legacy_relationships = item.get("relationships")
                relationships_data = legacy_relationships if isinstance(legacy_relationships, list) else []

            for rel in relationships_data:
                if not isinstance(rel, dict):
                    continue

                target_name = (rel.get("target_character_name") or "").strip()
                if not target_name:
                    continue

                target_char = character_name_to_obj.get(target_name)
                if not target_char or target_char.is_organization:
                    continue
                if target_char.id == character.id:
                    continue

                pair = (character.id, target_char.id)
                if pair in relationship_pairs:
                    continue

                relationship_name = (rel.get("relationship_type") or "未知关系").strip()[:100]
                intimacy_level = max(-100, min(_to_int(rel.get("intimacy_level", 50), 50), 100))
                status = (rel.get("status") or "active")[:20]
                description = rel.get("description")
                if description is not None:
                    description = str(description)

                db.add(
                    CharacterRelationship(
                        project_id=project.id,
                        character_from_id=character.id,
                        character_to_id=target_char.id,
                        relationship_type_id=relationship_type_map.get(relationship_name),
                        relationship_name=relationship_name,
                        intimacy_level=intimacy_level,
                        status=status,
                        description=description,
                        source="ai",
                    )
                )
                relationship_pairs.add(pair)

        # 第四阶段：创建组织成员关系（优先使用角色上的 organization_memberships）
        for character, item in created_items:
            if character.is_organization:
                continue

            org_memberships = item.get("organization_memberships")
            if not isinstance(org_memberships, list):
                continue

            for membership in org_memberships:
                if not isinstance(membership, dict):
                    continue

                org_name = (membership.get("organization_name") or "").strip()
                if not org_name:
                    continue

                org = organization_name_to_obj.get(org_name)
                if not org:
                    continue

                pair = (org.id, character.id)
                if pair in member_pairs:
                    continue

                db.add(
                    OrganizationMember(
                        organization_id=org.id,
                        character_id=character.id,
                        position=(membership.get("position") or "成员")[:100],
                        rank=max(0, min(_to_int(membership.get("rank", 0), 0), 10)),
                        loyalty=max(0, min(_to_int(membership.get("loyalty", 50), 50), 100)),
                        joined_at=membership.get("joined_at"),
                        status=(membership.get("status") or "active")[:20],
                        source="ai",
                    )
                )
                member_pairs.add(pair)
                org.member_count = (org.member_count or 0) + 1

        # 第五阶段：回填组织对象里的 organization_members（按名称补充成员）
        for character, item in created_items:
            if not character.is_organization:
                continue

            org = organization_name_to_obj.get(character.name)
            if not org:
                continue

            member_names_raw = item.get("organization_members")
            member_names: list[str] = []
            if isinstance(member_names_raw, list):
                member_names = [str(name).strip() for name in member_names_raw if str(name).strip()]
            elif isinstance(member_names_raw, str) and member_names_raw.strip():
                member_names = [member_names_raw.strip()]

            for member_name in member_names:
                member_char = character_name_to_obj.get(member_name)
                if not member_char or member_char.is_organization:
                    continue

                pair = (org.id, member_char.id)
                if pair in member_pairs:
                    continue

                db.add(
                    OrganizationMember(
                        organization_id=org.id,
                        character_id=member_char.id,
                        position="成员",
                        rank=0,
                        loyalty=50,
                        status="active",
                        source="ai",
                    )
                )
                member_pairs.add(pair)
                org.member_count = (org.member_count or 0) + 1

        await db.flush()
        return created

    async def _generate_relationships_for_existing_characters(
        self,
        *,
        db: AsyncSession,
        user_id: str,
        project: Project,
    ) -> int:
        """当角色已存在但缺少结构化关系时，只补角色关系。"""
        characters_result = await db.execute(
            select(Character)
            .where(
                Character.project_id == project.id,
                Character.is_organization == False,
            )
            .order_by(Character.created_at)
            .limit(40)
        )
        characters = characters_result.scalars().all()
        if len(characters) < 2:
            return 0

        existing_rel_result = await db.execute(
            select(CharacterRelationship.character_from_id, CharacterRelationship.character_to_id)
            .where(CharacterRelationship.project_id == project.id)
        )
        relationship_pairs: set[tuple[str, str]] = set()
        for source_id, target_id in existing_rel_result.all():
            relationship_pairs.add((source_id, target_id))
            relationship_pairs.add((target_id, source_id))

        character_by_name = {character.name: character for character in characters if character.name}
        characters_context = "\n".join(
            [
                (
                    f"- {character.name}｜定位:{character.role_type or '未设定'}"
                    f"｜性格:{(character.personality or '')[:120]}"
                    f"｜背景:{(character.background or '')[:160]}"
                )
                for character in characters
            ]
        )

        prompt = f"""你是专业的长篇小说角色关系架构师。请基于已有角色资料，为项目补全结构化角色关系。

【项目】
书名：{project.title or '未命名'}
类型：{project.genre or '未设定'}
主题：{project.theme or '未设定'}
世界观：{project.world_rules or project.world_atmosphere or project.world_location or '未设定'}

【已有角色】
{characters_context}

【输出要求】
只输出纯 JSON 数组，不要输出 markdown 或解释。
每条关系必须引用上方已有角色名称，不能发明新角色。
优先补主角、反派、关键配角之间的强关系，数量控制在 4 到 12 条。

[
  {{
    "source_character_name": "关系发起方角色名",
    "target_character_name": "关系目标角色名",
    "relationship_type": "师徒/朋友/敌人/竞争对手/家族/同门/合作伙伴等",
    "intimacy_level": 70,
    "status": "active",
    "description": "关系形成原因与剧情作用，80字以内"
  }}
]"""

        ai_service = await self._build_user_ai_service(db=db, user_id=user_id)
        raw_relationships = await self._call_ai_json_with_resilient_retry(
            ai_service,
            prompt=prompt,
            expected_type="array",
            label="既有角色关系补全",
            max_attempts=8,
        )
        if not isinstance(raw_relationships, list):
            return 0

        relationship_type_result = await db.execute(select(RelationshipType))
        relationship_type_map = {
            item.name: item.id
            for item in relationship_type_result.scalars().all()
            if item.name
        }

        def _to_int(value: Any, default: int) -> int:
            try:
                return int(value)
            except (TypeError, ValueError):
                return default

        created = 0
        for item in raw_relationships:
            if not isinstance(item, dict):
                continue
            source_name = str(
                item.get("source_character_name")
                or item.get("character_from_name")
                or item.get("source")
                or ""
            ).strip()
            target_name = str(
                item.get("target_character_name")
                or item.get("character_to_name")
                or item.get("target")
                or ""
            ).strip()
            source = character_by_name.get(source_name)
            target = character_by_name.get(target_name)
            if not source or not target or source.id == target.id:
                continue

            pair = (source.id, target.id)
            if pair in relationship_pairs:
                continue

            relationship_name = str(item.get("relationship_type") or "未知关系").strip()[:100]
            status = str(item.get("status") or "active").strip()[:20]
            if status not in {"active", "broken", "past", "complicated"}:
                status = "active"

            db.add(
                CharacterRelationship(
                    project_id=project.id,
                    character_from_id=source.id,
                    character_to_id=target.id,
                    relationship_type_id=relationship_type_map.get(relationship_name),
                    relationship_name=relationship_name,
                    intimacy_level=max(-100, min(_to_int(item.get("intimacy_level", 50), 50), 100)),
                    status=status,
                    description=str(item.get("description") or "").strip()[:500] or None,
                    source="ai",
                )
            )
            relationship_pairs.add((source.id, target.id))
            relationship_pairs.add((target.id, source.id))
            created += 1

        logger.info(f"拆书导入：项目 {project.id} 已补全既有角色关系 {created} 条")
        return created

    async def _generate_memberships_for_existing_organizations(
        self,
        *,
        db: AsyncSession,
        user_id: str,
        project: Project,
    ) -> int:
        """当组织和角色已存在但缺少成员关系时，只补组织成员。"""
        org_result = await db.execute(
            select(Organization, Character)
            .join(Character, Organization.character_id == Character.id)
            .where(Organization.project_id == project.id)
            .order_by(Character.created_at)
            .limit(20)
        )
        org_rows = org_result.all()
        if not org_rows:
            return 0

        characters_result = await db.execute(
            select(Character)
            .where(
                Character.project_id == project.id,
                Character.is_organization == False,
            )
            .order_by(Character.created_at)
            .limit(60)
        )
        characters = characters_result.scalars().all()
        if not characters:
            return 0

        existing_member_result = await db.execute(
            select(OrganizationMember.organization_id, OrganizationMember.character_id)
            .join(Organization, OrganizationMember.organization_id == Organization.id)
            .where(Organization.project_id == project.id)
        )
        member_pairs = {(row[0], row[1]) for row in existing_member_result.all()}

        organization_by_name = {
            org_character.name: organization
            for organization, org_character in org_rows
            if org_character.name
        }
        character_by_name = {character.name: character for character in characters if character.name}
        org_context = "\n".join(
            [
                (
                    f"- {org_character.name}｜类型:{org_character.organization_type or '未设定'}"
                    f"｜目的:{org_character.organization_purpose or ''}"
                    f"｜地点:{organization.location or ''}"
                )
                for organization, org_character in org_rows
            ]
        )
        character_context = "\n".join(
            [
                f"- {character.name}｜定位:{character.role_type or '未设定'}｜背景:{(character.background or '')[:120]}"
                for character in characters
            ]
        )

        prompt = f"""你是专业的小说组织关系设计师。请基于已有组织和角色，为项目补全组织成员关系。

【项目】
书名：{project.title or '未命名'}
类型：{project.genre or '未设定'}
主题：{project.theme or '未设定'}

【已有组织】
{org_context}

【已有角色】
{character_context}

【输出要求】
只输出纯 JSON 数组，不要输出 markdown 或解释。
organization_name 和 character_name 必须精确引用上方名称，不能发明新组织或新角色。
每个组织优先补 1 到 4 个关键成员，避免无意义铺量。

[
  {{
    "organization_name": "组织名",
    "character_name": "角色名",
    "position": "职位",
    "rank": 5,
    "loyalty": 80,
    "status": "active"
  }}
]"""

        ai_service = await self._build_user_ai_service(db=db, user_id=user_id)
        raw_memberships = await self._call_ai_json_with_resilient_retry(
            ai_service,
            prompt=prompt,
            expected_type="array",
            label="既有组织成员补全",
            max_attempts=8,
        )
        if not isinstance(raw_memberships, list):
            return 0

        def _to_int(value: Any, default: int) -> int:
            try:
                return int(value)
            except (TypeError, ValueError):
                return default

        created = 0
        for item in raw_memberships:
            if not isinstance(item, dict):
                continue
            org_name = str(item.get("organization_name") or "").strip()
            character_name = str(item.get("character_name") or "").strip()
            organization = organization_by_name.get(org_name)
            character = character_by_name.get(character_name)
            if not organization or not character:
                continue

            pair = (organization.id, character.id)
            if pair in member_pairs:
                continue

            status = str(item.get("status") or "active").strip()[:20]
            if status not in {"active", "retired", "expelled", "deceased"}:
                status = "active"

            db.add(
                OrganizationMember(
                    organization_id=organization.id,
                    character_id=character.id,
                    position=(str(item.get("position") or "成员").strip() or "成员")[:100],
                    rank=max(0, min(_to_int(item.get("rank", 0), 0), 10)),
                    loyalty=max(0, min(_to_int(item.get("loyalty", 50), 50), 100)),
                    status=status,
                    source="ai",
                )
            )
            organization.member_count = (organization.member_count or 0) + 1
            member_pairs.add(pair)
            created += 1

        logger.info(f"拆书导入：项目 {project.id} 已补全既有组织成员 {created} 条")
        return created

    def _start_post_import_followup(
        self,
        *,
        user_id: str,
        project_id: str,
        character_count: int,
    ) -> None:
        """在导入落库完成后，后台继续补全世界观、职业和角色。"""
        existing_task = self._post_import_followup_tasks.get(project_id)
        if existing_task and not existing_task.done():
            logger.info(f"拆书导入：项目 {project_id} 的后续补全任务已在运行中")
            return

        self._post_import_followup_states[project_id] = {
            "status": "running",
            "started_at": datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow().isoformat(),
            "error": None,
        }

        followup_task = asyncio.create_task(
            self._run_post_import_followup(
                user_id=user_id,
                project_id=project_id,
                character_count=character_count,
            )
        )
        self._post_import_followup_tasks[project_id] = followup_task

        def _cleanup(done_task: asyncio.Task) -> None:
            if self._post_import_followup_tasks.get(project_id) is done_task:
                self._post_import_followup_tasks.pop(project_id, None)
            if done_task.cancelled():
                logger.warning(f"拆书导入：项目 {project_id} 的后续补全任务已取消")
                self._post_import_followup_states[project_id] = {
                    **self._post_import_followup_states.get(project_id, {}),
                    "status": "cancelled",
                    "updated_at": datetime.utcnow().isoformat(),
                }
                return
            exc = done_task.exception()
            if exc:
                self._post_import_followup_states[project_id] = {
                    **self._post_import_followup_states.get(project_id, {}),
                    "status": "failed",
                    "updated_at": datetime.utcnow().isoformat(),
                    "error": str(exc),
                }
                logger.error(
                    f"拆书导入：项目 {project_id} 的后续补全任务异常退出: {exc}",
                    exc_info=(type(exc), exc, exc.__traceback__),
                )

        followup_task.add_done_callback(_cleanup)
        logger.info(f"拆书导入：项目 {project_id} 已启动后台补全任务")

    async def _run_post_import_followup(
        self,
        *,
        user_id: str,
        project_id: str,
        character_count: int,
    ) -> None:
        """在后台继续生成世界观、职业体系和角色/组织。"""
        engine = await get_engine(user_id)
        AsyncSessionLocal = async_sessionmaker(
            engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

        try:
            async with AsyncSessionLocal() as db:
                project = await verify_project_access(project_id, user_id, db)
                await self._run_post_import_wizard_generation(
                    db=db,
                    user_id=user_id,
                    project=project,
                    character_count=character_count,
                )
                await db.commit()
                self._post_import_followup_states[project_id] = {
                    **self._post_import_followup_states.get(project_id, {}),
                    "status": "completed",
                    "updated_at": datetime.utcnow().isoformat(),
                    "error": None,
                }
                logger.info(f"拆书导入：项目 {project_id} 的后台补全任务已完成")
        except Exception as exc:
            self._post_import_followup_states[project_id] = {
                **self._post_import_followup_states.get(project_id, {}),
                "status": "failed",
                "updated_at": datetime.utcnow().isoformat(),
                "error": str(exc),
            }
            logger.warning(f"拆书导入：项目 {project_id} 后台补全失败: {exc}", exc_info=True)

    async def get_post_import_followup_status(
        self,
        *,
        project_id: str,
        user_id: str,
        db: AsyncSession,
    ) -> dict[str, Any]:
        project = await verify_project_access(project_id, user_id, db)
        counts = await self._load_project_followup_counts(db=db, project_id=project_id)

        analysis_status_result = await db.execute(
            select(AnalysisTask.status, func.count())
            .where(AnalysisTask.project_id == project_id)
            .group_by(AnalysisTask.status)
        )
        analysis_tasks = {
            str(status): int(count or 0)
            for status, count in analysis_status_result.all()
        }
        total_analysis_tasks = sum(analysis_tasks.values())
        completed_analysis_tasks = int(analysis_tasks.get("completed", 0))

        followup_task = self._post_import_followup_tasks.get(project_id)
        followup_running = bool(followup_task and not followup_task.done())
        followup_state = dict(self._post_import_followup_states.get(project_id) or {})

        world_completed = bool(
            project.world_time_period
            or project.world_location
            or project.world_atmosphere
            or project.world_rules
        )
        missing_steps: list[str] = []
        if not world_completed:
            missing_steps.append("world_building")
        if counts["careers"] <= 0:
            missing_steps.append("career_system")
        if counts["characters"] <= 0:
            missing_steps.append("characters")
        if counts["organizations"] <= 0 and counts["organization_characters"] <= 0:
            missing_steps.append("organizations")
        if counts["relationships"] <= 0 and counts["characters"] > 1:
            missing_steps.append("relationships")
        if counts["organization_members"] <= 0 and counts["organizations"] > 0 and counts["characters"] > 0:
            missing_steps.append("organization_members")

        if followup_running:
            status = "running"
        elif missing_steps:
            status = "needs_action"
        elif total_analysis_tasks and completed_analysis_tasks < total_analysis_tasks:
            status = "analysis_running"
        else:
            status = "completed"

        return {
            "success": True,
            "project_id": project_id,
            "status": status,
            "followup_running": followup_running,
            "followup_state": followup_state,
            "missing_steps": missing_steps,
            "world_completed": world_completed,
            "counts": counts,
            "analysis_tasks": {
                "total": total_analysis_tasks,
                "completed": completed_analysis_tasks,
                "failed": int(analysis_tasks.get("failed", 0)),
                "running": int(analysis_tasks.get("running", 0)),
                "pending": int(analysis_tasks.get("pending", 0)),
                "by_status": analysis_tasks,
            },
        }

    async def resume_post_import_followup(
        self,
        *,
        project_id: str,
        user_id: str,
        db: AsyncSession,
    ) -> dict[str, Any]:
        project = await verify_project_access(project_id, user_id, db)
        self._start_post_import_followup(
            user_id=user_id,
            project_id=project_id,
            character_count=max(project.character_count or 0, 8),
        )
        status = await self.get_post_import_followup_status(project_id=project_id, user_id=user_id, db=db)
        status["message"] = "已重新启动项目补全任务"
        return status

    async def resume_post_import_pipeline(
        self,
        *,
        project_id: str,
        user_id: str,
        db: AsyncSession,
    ) -> dict[str, int | bool | str]:
        """手动重新启动导入后的后台补全与章节分析。"""
        project = await verify_project_access(project_id, user_id, db)

        chapters_result = await db.execute(
            select(Chapter)
            .where(Chapter.project_id == project_id)
            .order_by(Chapter.chapter_number)
        )
        chapters = chapters_result.scalars().all()
        chapter_id_map = {
            chapter.chapter_number: chapter.id
            for chapter in chapters
            if chapter.id
        }

        chapter_ids = list(chapter_id_map.values())
        existing_tasks_result = await db.execute(
            select(AnalysisTask)
            .where(AnalysisTask.chapter_id.in_(chapter_ids))
            .order_by(AnalysisTask.chapter_id, AnalysisTask.created_at.desc())
        )
        existing_tasks = existing_tasks_result.scalars().all()
        latest_task_map: dict[str, AnalysisTask] = {}
        for task in existing_tasks:
            if task.chapter_id not in latest_task_map:
                latest_task_map[task.chapter_id] = task

        analysis_queue: list[dict[str, int | str]] = []
        for chapter in chapters:
            if not chapter.content or chapter.content.strip() == "":
                continue

            latest_task = latest_task_map.get(chapter.id)
            if latest_task and latest_task.status == "completed":
                continue
            if latest_task and latest_task.status in {"pending", "running"}:
                analysis_queue.append(
                    {
                        "chapter_id": chapter.id,
                        "chapter_number": chapter.chapter_number,
                        "task_id": latest_task.id,
                    }
                )
                continue

            analysis_task = AnalysisTask(
                chapter_id=chapter.id,
                user_id=user_id,
                project_id=project_id,
                status="pending",
                progress=0,
            )
            db.add(analysis_task)
            await db.flush()
            analysis_queue.append(
                {
                    "chapter_id": chapter.id,
                    "chapter_number": chapter.chapter_number,
                    "task_id": analysis_task.id,
                }
            )

        await db.commit()

        self._start_post_import_followup(
            user_id=user_id,
            project_id=project_id,
            character_count=max(project.character_count or 0, 8),
        )
        self._start_post_import_analysis(
            user_id=user_id,
            project_id=project_id,
            tasks_queue=analysis_queue,
        )

        return {
            "success": True,
            "project_id": project_id,
            "analysis_tasks": len(analysis_queue),
            "followup_started": True,
            "message": "已重新启动后台导入补全任务",
        }

    async def _create_post_import_analysis_tasks(
        self,
        *,
        db: AsyncSession,
        user_id: str,
        project_id: str,
        chapter_id_map: dict[int, str],
    ) -> list[dict[str, int | str]]:
        """为本次导入的章节预创建分析任务，等待导入事务提交后再启动。"""
        if not chapter_id_map:
            return []

        chapter_ids = list(chapter_id_map.values())
        chapters_result = await db.execute(
            select(Chapter)
            .where(Chapter.id.in_(chapter_ids))
            .order_by(Chapter.chapter_number)
        )
        chapters = chapters_result.scalars().all()

        existing_tasks_result = await db.execute(
            select(AnalysisTask)
            .where(AnalysisTask.chapter_id.in_(chapter_ids))
            .order_by(AnalysisTask.chapter_id, AnalysisTask.created_at.desc())
        )
        existing_tasks = existing_tasks_result.scalars().all()
        latest_task_map: dict[str, AnalysisTask] = {}
        for task in existing_tasks:
            if task.chapter_id not in latest_task_map:
                latest_task_map[task.chapter_id] = task

        tasks_queue: list[dict[str, int | str]] = []
        for chapter in chapters:
            if not chapter.content or chapter.content.strip() == "":
                continue

            latest_task = latest_task_map.get(chapter.id)
            if latest_task and latest_task.status in {"pending", "running", "completed"}:
                continue

            analysis_task = AnalysisTask(
                chapter_id=chapter.id,
                user_id=user_id,
                project_id=project_id,
                status="pending",
                progress=0,
            )
            db.add(analysis_task)
            await db.flush()
            tasks_queue.append(
                {
                    "chapter_id": chapter.id,
                    "chapter_number": chapter.chapter_number,
                    "task_id": analysis_task.id,
                }
            )

        if tasks_queue:
            logger.info(f"拆书导入：已为项目 {project_id} 创建 {len(tasks_queue)} 个章节分析任务")

        return tasks_queue

    def _start_post_import_analysis(
        self,
        *,
        user_id: str,
        project_id: str,
        tasks_queue: list[dict[str, int | str]],
    ) -> None:
        """导入事务提交成功后，启动后台顺序章节分析。"""
        if not tasks_queue:
            return

        analysis_task = asyncio.create_task(
            self._run_post_import_analysis_queue(
                user_id=user_id,
                project_id=project_id,
                tasks_queue=tasks_queue,
            )
        )
        self._post_import_analysis_tasks.add(analysis_task)

        def _cleanup(done_task: asyncio.Task) -> None:
            self._post_import_analysis_tasks.discard(done_task)
            if done_task.cancelled():
                logger.warning(f"拆书导入：项目 {project_id} 的后台章节分析任务已取消")
                return
            exc = done_task.exception()
            if exc:
                logger.error(
                    f"拆书导入：项目 {project_id} 的后台章节分析任务异常退出: {exc}",
                    exc_info=(type(exc), exc, exc.__traceback__),
                )

        analysis_task.add_done_callback(_cleanup)
        logger.info(f"拆书导入：项目 {project_id} 已启动后台章节分析队列，共 {len(tasks_queue)} 章")

    async def _run_post_import_analysis_queue(
        self,
        *,
        user_id: str,
        project_id: str,
        tasks_queue: list[dict[str, int | str]],
    ) -> None:
        """复用章节模块的一键分析队列，按章节顺序后台分析导入章节。"""
        engine = await get_engine(user_id)
        AsyncSessionLocal = async_sessionmaker(
            engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

        try:
            async with AsyncSessionLocal() as db:
                ai_service = await self._build_user_ai_service(db=db, user_id=user_id)

                from app.api.chapters import _run_batch_analysis_in_sequence

                await _run_batch_analysis_in_sequence(
                    tasks_queue=tasks_queue,
                    user_id=user_id,
                    project_id=project_id,
                    ai_service=ai_service,
                )
                logger.info(f"拆书导入：项目 {project_id} 后台章节分析队列执行完成")
        except Exception as exc:
            error_message = f"导入后自动章节分析失败：{exc}"
            logger.error(error_message, exc_info=True)
            await self._mark_post_import_analysis_tasks_failed(
                user_id=user_id,
                tasks_queue=tasks_queue,
                error_message=error_message,
            )

    async def _mark_post_import_analysis_tasks_failed(
        self,
        *,
        user_id: str,
        tasks_queue: list[dict[str, int | str]],
        error_message: str,
    ) -> None:
        """后台分析无法启动时，明确更新任务状态，避免前端一直看到 pending。"""
        task_ids = [str(item["task_id"]) for item in tasks_queue if item.get("task_id")]
        if not task_ids:
            return

        try:
            engine = await get_engine(user_id)
            AsyncSessionLocal = async_sessionmaker(
                engine,
                class_=AsyncSession,
                expire_on_commit=False,
            )
            async with AsyncSessionLocal() as db:
                result = await db.execute(select(AnalysisTask).where(AnalysisTask.id.in_(task_ids)))
                tasks = result.scalars().all()
                now = datetime.now()
                for task in tasks:
                    if task.status in {"completed", "failed"}:
                        continue
                    task.status = "failed"
                    task.progress = 0
                    task.error_message = error_message[:500]
                    task.completed_at = now
                await db.commit()
        except Exception as exc:
            logger.error(f"拆书导入：标记自动分析任务失败时异常: {exc}", exc_info=True)

    def _build_summary(self, content: str, max_len: int = 120) -> Optional[str]:
        if not content:
            return None
        normalized = re.sub(r"\s+", " ", content).strip()
        if len(normalized) <= max_len:
            return normalized
        return normalized[:max_len] + "…"

    def _task_snapshot_path(self, task_id: str) -> Path:
        if not BOOK_IMPORT_TASK_ID_RE.fullmatch(task_id or ""):
            raise HTTPException(status_code=404, detail="任务不存在")
        return BOOK_IMPORT_TASK_STATE_DIR / f"{task_id}.json"

    def _persist_task_snapshot(self, task: _BookImportTask) -> None:
        try:
            BOOK_IMPORT_TASK_STATE_DIR.mkdir(parents=True, exist_ok=True)
            path = self._task_snapshot_path(task.task_id)
            temp_path = path.with_suffix(".json.tmp")
            temp_path.write_text(
                json.dumps(self._task_to_snapshot(task), ensure_ascii=False),
                encoding="utf-8",
            )
            temp_path.replace(path)
        except Exception:
            logger.warning("写入拆书任务快照失败: task_id=%s", task.task_id, exc_info=True)

    def _load_task_snapshot(self, task_id: str) -> _BookImportTask | None:
        try:
            path = self._task_snapshot_path(task_id)
        except HTTPException:
            return None
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            task = self._task_from_snapshot(data)
        except Exception:
            logger.warning("读取拆书任务快照失败: task_id=%s", task_id, exc_info=True)
            return None

        if task.status in {"pending", "running"}:
            age_seconds = (datetime.utcnow() - task.updated_at).total_seconds()
            if age_seconds > BOOK_IMPORT_TASK_STALE_SECONDS:
                self._set_task_state(
                    task,
                    status="failed",
                    progress=task.progress,
                    message="拆书任务已中断",
                    error="后端服务重启或任务长时间无更新，请重新上传 TXT 并开始解析",
                )
        return task

    def _task_to_snapshot(self, task: _BookImportTask) -> dict[str, Any]:
        return {
            "task_id": task.task_id,
            "user_id": task.user_id,
            "filename": task.filename,
            "project_id": task.project_id,
            "create_new_project": task.create_new_project,
            "import_mode": task.import_mode,
            "extract_mode": task.extract_mode,
            "tail_chapter_count": task.tail_chapter_count,
            "status": task.status,
            "progress": task.progress,
            "message": task.message,
            "error": task.error,
            "created_at": task.created_at.isoformat(),
            "updated_at": task.updated_at.isoformat(),
            "preview": task.preview.model_dump(mode="json") if task.preview else None,
            "cancelled": task.cancelled,
            "imported_project_id": task.imported_project_id,
            "failed_steps": [
                {
                    "step_name": failure.step_name,
                    "step_label": failure.step_label,
                    "error_message": failure.error_message,
                    "retry_count": failure.retry_count,
                }
                for failure in task.failed_steps
            ],
        }

    def _task_from_snapshot(self, data: dict[str, Any]) -> _BookImportTask:
        preview_data = data.get("preview")
        failed_steps_data = data.get("failed_steps")
        return _BookImportTask(
            task_id=str(data.get("task_id") or ""),
            user_id=str(data.get("user_id") or ""),
            filename=str(data.get("filename") or ""),
            project_id=data.get("project_id") if isinstance(data.get("project_id"), str) else None,
            create_new_project=bool(data.get("create_new_project", True)),
            import_mode=str(data.get("import_mode") or "append"),
            extract_mode=data.get("extract_mode") if data.get("extract_mode") in {"tail", "full"} else "full",
            tail_chapter_count=int(data.get("tail_chapter_count") or 10),
            status=str(data.get("status") or "failed"),
            progress=int(data.get("progress") or 0),
            message=data.get("message") if isinstance(data.get("message"), str) else None,
            error=data.get("error") if isinstance(data.get("error"), str) else None,
            created_at=self._parse_snapshot_datetime(data.get("created_at")),
            updated_at=self._parse_snapshot_datetime(data.get("updated_at")),
            preview=BookImportPreviewResponse.model_validate(preview_data) if isinstance(preview_data, dict) else None,
            cancelled=bool(data.get("cancelled", False)),
            imported_project_id=data.get("imported_project_id") if isinstance(data.get("imported_project_id"), str) else None,
            failed_steps=[
                _StepFailure(
                    step_name=str(item.get("step_name") or ""),
                    step_label=str(item.get("step_label") or ""),
                    error_message=str(item.get("error_message") or item.get("error") or ""),
                    retry_count=int(item.get("retry_count") or 0),
                )
                for item in failed_steps_data
                if isinstance(item, dict)
            ] if isinstance(failed_steps_data, list) else [],
        )

    def _parse_snapshot_datetime(self, value: Any) -> datetime:
        if isinstance(value, datetime):
            parsed = value
        else:
            try:
                parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
            except ValueError:
                return datetime.utcnow()
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed

    async def _get_task(self, *, task_id: str, user_id: str) -> _BookImportTask:
        async with self._tasks_lock:
            task = self._tasks.get(task_id)

        snapshot_task = self._load_task_snapshot(task_id)
        if snapshot_task and (
            not task
            or snapshot_task.updated_at >= task.updated_at
        ):
            task = snapshot_task
            async with self._tasks_lock:
                self._tasks[task_id] = task

        if not task:
            raise HTTPException(status_code=404, detail="任务不存在")
        if task.user_id != user_id:
            raise HTTPException(status_code=403, detail="无权访问该任务")
        return task

    def _to_status(self, task: _BookImportTask) -> BookImportTaskStatusResponse:
        return BookImportTaskStatusResponse(
            task_id=task.task_id,
            status=task.status,  # type: ignore[arg-type]
            progress=task.progress,
            message=task.message,
            error=task.error,
            created_at=task.created_at,
            updated_at=task.updated_at,
        )

    def _set_task_state(
        self,
        task: _BookImportTask,
        *,
        status: str,
        progress: int,
        message: Optional[str],
        error: Optional[str] = None,
    ) -> None:
        task.status = status
        task.progress = max(0, min(100, progress))
        task.message = message
        task.error = error
        task.updated_at = datetime.utcnow()
        self._persist_task_snapshot(task)

    def _check_cancelled(self, task: _BookImportTask) -> None:
        if task.cancelled or task.status == "cancelled":
            raise asyncio.CancelledError("任务已取消")


book_import_service = BookImportService()
