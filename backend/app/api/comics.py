"""漫画管理 API。"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
import json
import re
import uuid
from typing import Any, Literal
from threading import RLock

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Path as ApiPath, Request, status
from fastapi.responses import FileResponse, RedirectResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.common import verify_project_access
from app.api.settings import get_user_ai_settings
from app.database import get_db, get_engine
from app.logger import get_logger
from app.models.chapter import Chapter
from app.models.character import Character
from app.models.memory import PlotAnalysis
from app.models.media_artifact import CharacterImageArtifact, ComicPageArtifact, ComicStoryboardArtifact
from app.models.project import Project
from app.models.settings import Settings as UserSettings
from app.models.mcp_plugin import MCPPlugin
from app.services.ai_config import AIClientConfig, HTTPClientConfig
from app.services.ai_service import AIService
from app.services.agents import ComicWorkflowAgent, StoryboardWorkflowAgent
from app.services.comic_style import build_comic_style_instruction
from app.services.comic_pipeline_utils import (
    chapter_has_content as _shared_chapter_has_content,
    chapter_pipeline_context_summary as _shared_chapter_pipeline_context_summary,
    filter_missing_comic_page_numbers as _shared_filter_missing_comic_page_numbers,
    page_has_image as _shared_page_has_image,
    resolve_analysis_stage_action as _shared_resolve_analysis_stage_action,
    should_retry_comic_image_error as _shared_should_retry_comic_image_error,
)
from app.services.image_request_utils import (
    build_image_edit_payload,
    build_image_generation_payload,
    build_dialogue_reference,
    build_visible_text_rule,
    decode_b64_image_response,
    normalize_image_api_base_urls,
    normalize_image_bytes_to_png,
    normalize_image_text_language,
    resolve_image_api_base_url,
    resolve_image_edit_model,
    resolve_image_provider_profile,
)
from app.services.storyboard_prompt_cache import (
    is_storyboard_prompt_fresh as _shared_is_storyboard_prompt_fresh,
    load_storyboard_prompt_metadata as _shared_load_storyboard_prompt_metadata,
    storyboard_prompt_context_hash as _shared_storyboard_prompt_context_hash,
    storyboard_prompt_metadata_path as _shared_storyboard_prompt_metadata_path,
    storyboard_prompt_request_summary as _shared_storyboard_prompt_request_summary,
    write_storyboard_prompt_metadata as _shared_write_storyboard_prompt_metadata,
)
from app.services.json_helper import parse_json
from app.services.tencent_cos_storage import COSObjectMetadata, tencent_cos_storage

router = APIRouter(prefix="/comics", tags=["漫画管理"])
logger = get_logger(__name__)

COMIC_STATE_ROOT = Path("/tmp/mumuainovel_comic_state").resolve()
STORYBOARD_FILE_RE = re.compile(r"^chapter_(\d+)_storyboard\.(json|md)$")
CHAPTER_DIR_RE = re.compile(r"^chapter_(\d+)$")
PAGE_FILE_RE = re.compile(r"^page_(\d+)(?:__.+)?\.png$")
PROMPT_FILE_RE = re.compile(r"^page_(\d+)_prompt\.txt$")
FAILED_PAGE_KEY_RE = re.compile(r"^(?P<chapter>\d+)-(?P<page>\d+)$")
REGEN_QUEUE_FILE = "regen_queue.jsonl"
REGEN_STATE_FILE = "regen_tasks.json"
PIPELINE_STATE_FILE = "pipeline_batch.json"
CHARACTER_IMAGE_ROOT = (Path(__file__).parent.parent.parent / "storage" / "character_images").resolve()
DEFAULT_CHARACTER_IMAGE_VARIANT = "default"
COMIC_PAGE_BATCH_CONCURRENCY_DEFAULT = 2
COMIC_PAGE_BATCH_CONCURRENCY_MAX = 6
COMIC_IMAGE_GENERATION_TIMEOUT_SECONDS = 900.0
COMIC_IMAGE_CONNECT_TIMEOUT_SECONDS = 15.0
COMIC_IMAGE_WRITE_TIMEOUT_SECONDS = 60.0
COMIC_IMAGE_POOL_TIMEOUT_SECONDS = 30.0
COMIC_IMAGE_FIRST_PROMPT_MAX_ATTEMPTS = 4
COMIC_IMAGE_REWRITE_PROMPT_MAX_ATTEMPTS = 2
ORPHANED_BATCH_TASK_MESSAGE = "服务已重启，后台批量任务已中断，请重新启动任务"
_STATE_LOCKS: dict[str, RLock] = {}
_STATE_LOCKS_GUARD = RLock()


def _require_cos_image_storage() -> None:
    if not tencent_cos_storage.is_enabled():
        raise RuntimeError("Tencent COS 未配置，漫画图片只保存 COS 地址，不再保存本地文件")


def _normalize_comic_page_concurrency(value: int | None) -> int:
    if value is None:
        return COMIC_PAGE_BATCH_CONCURRENCY_DEFAULT
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return COMIC_PAGE_BATCH_CONCURRENCY_DEFAULT
    return max(1, min(parsed, COMIC_PAGE_BATCH_CONCURRENCY_MAX))


def _project_comic_style_instruction(project: Project | None) -> str:
    return build_comic_style_instruction(
        getattr(project, "comic_style", None),
        getattr(project, "comic_style_prompt", None),
    )


def _append_project_comic_style(prompt: str, project: Project | None) -> str:
    instruction = _project_comic_style_instruction(project)
    if instruction in prompt:
        return prompt
    return f"{prompt.strip()}\n\n{instruction}".strip()

SENSITIVE_COMIC_PROMPT_REPLACEMENTS = (
    (r"双修|房中术|采补|炉鼎|合欢", "灵力交汇的含蓄双修仪式，角色在轻薄丝绸与流动光影中保持亲密距离"),
    (r"性爱|性交|交合|做爱|云雨|春宵|交欢|欢好|缠绵床榻|床笫", "床榻上的亲密拥抱，身体轮廓被半透明丝被与柔和阴影遮挡，以暧昧姿态表现情绪张力"),
    (r"亲吻|热吻|深吻|舌吻|吻遍", "亲密接吻，唇部贴近，距离感强烈"),
    (r"抚摸|爱抚|挑逗|撩拨|摩挲", "手部隔着轻薄衣料沿身体曲线轻抚，以含蓄触碰表现暧昧"),
    (r"脱衣|宽衣|褪衣|衣衫半解|衣衫不整", "衣衫松散滑落肩头，半透明内衫以光影遮挡身体重点部位"),
    (r"裸露|裸体|赤裸|露点|下体|私处|阴部|阳具|勃起", "角色被轻薄纱衣与丝绸包裹，布料、阴影和镜头角度进行策略性遮挡"),
    (r"臀部|屁股|翘臀", "穿着贴身轻纱服饰的优雅身体曲线"),
    (r"呻吟|娇喘|喘息|喘吟", "脸颊泛红、唇部微张，以表情暗示呼吸急促"),
    (r"高潮|射精|体液|精液|淫液|湿润", "灵力在角色之间爆发，发光气息包裹亲密身影"),
    (r"欲望|情欲|媚态|淫靡|色情|情色|香艳", "带有浪漫张力和暧昧吸引力的氛围"),
)

# 年龄相关描述替换：将暗示未成年/幼态的描述替换为成年女性描述
AGE_DESCRIPTOR_REPLACEMENTS = (
    (r"稚嫩", "年轻但成熟的成年人气质"),
    (r"娇小", "身形纤细的成年女性"),
    (r"幼小", "年轻成年女性"),
    (r"幼女|萝莉|loli|lo娘", "年轻成年女性"),
    (r"未成年|未发育|未长成", "年轻成年人"),
    (r"童颜|娃娃脸|孩子气", "面容显年轻的成年女性"),
    (r"处子|处女", "年轻成年女性"),
    (r"青涩", "清新年轻的成年人气质"),
    (r"年纪小|年龄小|年幼|年少|小小年纪", "年轻成年人"),
    (r"小女孩|小姑娘|小丫头|小女娃|小女子|女童|女孩", "年轻成年女性"),
    (r"幼态|婴儿肥", "显年轻的成年面部特征"),
    (r"初经人事|初次|初夜", "第一次亲密经历"),
)

SENSITIVE_COMIC_PROMPT_RULE = (
    "敏感内容改写规则：如果原始场景包含性或亲密内容，保留浪漫/亲密剧情脉络，"
    "但以含蓄、非露骨的方式呈现。使用半透明布料、策略性镜头、蒸汽/薄雾、"
    "发丝遮挡关键部位、交织剪影、泛红肤色和暗示激情的肢体语言等视觉手法。"
    "女性角色可以穿轻薄半透明的薄纱或丝质服饰，通过衣料呈现身体轮廓和肤色，"
    "但不得呈现露点、性行为或身体私密部位。必须保留亲密感与情绪张力，"
    "不要完全净化剧情或删除浪漫内容。所有角色必须呈现为成年人。"
)


def _character_image_state_file(project_id: str) -> Path:
    state_file = (CHARACTER_IMAGE_ROOT / project_id / "state.json").resolve()
    try:
        state_file.relative_to(CHARACTER_IMAGE_ROOT)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="非法文件路径") from exc
    return state_file


def _read_character_image_state(project_id: str) -> dict[str, Any]:
    state_file = _character_image_state_file(project_id)
    if not state_file.is_file():
        return {}
    key = str(state_file)
    with _STATE_LOCKS_GUARD:
        lock = _STATE_LOCKS.setdefault(key, RLock())
    try:
        with lock:
            if not state_file.is_file():
                return {}
            data = json.loads(state_file.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except Exception as exc:
        logger.warning("读取角色形象状态失败: %s (%s)", state_file, exc)
        return {}


def _character_variant_image_url(character_id: str, variant_key: str | None = None) -> str:
    if variant_key and variant_key != DEFAULT_CHARACTER_IMAGE_VARIANT:
        return f"/api/character-images/characters/{character_id}/variants/{variant_key}/image"
    return f"/api/character-images/characters/{character_id}/image"


def _new_comic_page_file(project_id: str, chapter_number: int, page_number: int) -> Path:
    chapter_dir = _project_root(project_id) / "manhua" / f"chapter_{chapter_number:04d}"
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
    suffix = uuid.uuid4().hex[:8]
    return chapter_dir / f"page_{page_number:02d}__{timestamp}_{suffix}.png"


_SEXUAL_CONTENT_PATTERN = re.compile(
    r"双修|房中术|性爱|性交|交合|做爱|云雨|春宵|交欢|欢好|缠绵床榻|床笫|"
    r"亲吻|热吻|深吻|抚摸|爱抚|脱衣|裸露|裸体|赤裸|胸部|乳房|臀部|"
    r"呻吟|娇喘|高潮|射精|体液|精液|淫液|欲望|情欲|媚态|淫靡|色情|情色|香艳|"
    r"娇喘|喘息|挑逗|撩拨|私处|阴部|阳具|勃起|乳头|乳晕|下体|露点|"
    r"初经人事|初夜|处子|处女",
    re.IGNORECASE,
)


def _soften_sensitive_comic_text(value: str, *, aggressive: bool = False) -> str:
    cleaned = str(value or "")
    # 仅在内容包含性相关描写时，才替换年龄相关描述，避免 AI 拒绝生成
    if _SEXUAL_CONTENT_PATTERN.search(cleaned):
        for pattern, replacement in AGE_DESCRIPTOR_REPLACEMENTS:
            cleaned = re.sub(pattern, replacement, cleaned, flags=re.IGNORECASE)
    for pattern, replacement in SENSITIVE_COMIC_PROMPT_REPLACEMENTS:
        cleaned = re.sub(pattern, replacement, cleaned, flags=re.IGNORECASE)
    if aggressive:
        cleaned = re.sub(
            r"亲密|暧昧|诱惑|魅惑|妩媚",
            "感官吸引力与浪漫张力",
            cleaned,
            flags=re.IGNORECASE,
        )
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


def _contains_cjk_text(value: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", value or ""))


def _with_comic_safety_rules(prompt: str, *, aggressive: bool = False, image_text_language: str | None = None) -> str:
    softened = _soften_sensitive_comic_text(prompt, aggressive=aggressive)
    rules = [
        SENSITIVE_COMIC_PROMPT_RULE,
        build_visible_text_rule(image_text_language, scope="comic"),
    ]
    if aggressive:
        rules.append(
            "重试模式：保留亲密场景的剧情含义，但使用更间接的视觉叙事，"
            "更多使用蒸汽、薄雾、策略性布料遮挡、帘幕后剪影以及脸部/表情特写，"
            "避免把镜头重点放在身体上，同时保持浪漫剧情清晰可读。"
        )
    suffix = "\n\n安全改写规则：\n" + "\n".join(f"- {rule}" for rule in rules)
    if "敏感内容改写规则：" in softened:
        return softened
    return f"{softened}{suffix}".strip()


def _comic_prompt_attempts(prompt: str, *, image_text_language: str | None = None) -> list[tuple[str, str]]:
    first_prompt = _with_comic_safety_rules(prompt, aggressive=False, image_text_language=image_text_language)
    retry_prompt = _with_comic_safety_rules(prompt, aggressive=True, image_text_language=image_text_language)
    attempts = [("softened", first_prompt)]
    if retry_prompt != first_prompt:
        attempts.append(("conservative_rewrite", retry_prompt))
    return attempts


def _normalize_storyboard_text(value: Any, *, limit: int | None = None) -> str:
    text = " ".join(str(value or "").split()).strip()
    if limit is not None and len(text) > limit:
        return text[:limit].rstrip() + "…"
    return text


def _load_jsonish(value: Any) -> Any:
    if value is None or isinstance(value, (dict, list)):
        return value
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        return None


def _extract_string_list(value: Any, *, limit: int | None = None) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        text = _normalize_storyboard_text(item, limit=limit)
        if text:
            result.append(text)
    return result


def _extract_entry_texts(
    value: Any,
    *,
    keys: tuple[str, ...],
    limit: int | None = None,
) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        if isinstance(item, dict):
            for key in keys:
                text = _normalize_storyboard_text(item.get(key), limit=limit)
                if text:
                    result.append(text)
                    break
        else:
            text = _normalize_storyboard_text(item, limit=limit)
            if text:
                result.append(text)
    return result


def _extract_visual_bible_snapshot(visual_bible: Any) -> dict[str, Any]:
    bible = _load_jsonish(visual_bible)
    if not isinstance(bible, dict):
        return {}
    snapshot: dict[str, Any] = {}
    trigger_token = _normalize_storyboard_text(bible.get("trigger_token"), limit=80)
    if trigger_token:
        snapshot["trigger_token"] = trigger_token
    immutable_traits = _extract_string_list(bible.get("immutable_traits"), limit=120)
    if immutable_traits:
        snapshot["immutable_traits"] = immutable_traits[:6]
    views = bible.get("views")
    if isinstance(views, list):
        snapshot["views"] = [
            {
                key: _normalize_storyboard_text(view.get(key), limit=120)
                for key in ("name", "description", "camera", "composition")
                if isinstance(view, dict) and _normalize_storyboard_text(view.get(key), limit=120)
            }
            for view in views[:3]
            if isinstance(view, dict)
        ]
    expressions = bible.get("expressions")
    if isinstance(expressions, list):
        snapshot["expressions"] = [
            {
                key: _normalize_storyboard_text(expr.get(key), limit=120)
                for key in ("name", "description")
                if isinstance(expr, dict) and _normalize_storyboard_text(expr.get(key), limit=120)
            }
            for expr in expressions[:4]
            if isinstance(expr, dict)
        ]
    outfits = bible.get("outfits")
    if isinstance(outfits, list):
        snapshot["outfits"] = [
            {
                key: _normalize_storyboard_text(outfit.get(key), limit=160)
                for key in ("name", "description", "scene")
                if isinstance(outfit, dict) and _normalize_storyboard_text(outfit.get(key), limit=160)
            }
            for outfit in outfits[:4]
            if isinstance(outfit, dict)
        ]
    training_caption = _normalize_storyboard_text(bible.get("training_caption"), limit=220)
    if training_caption:
        snapshot["training_caption"] = training_caption
    return snapshot


def _extract_analysis_scene_anchors(analysis: Any, source: str) -> list[dict[str, Any]]:
    anchors: list[dict[str, Any]] = []
    if not analysis:
        return anchors
    scenes = getattr(analysis, "scenes", None)
    scenes = _load_jsonish(scenes)
    if not isinstance(scenes, list):
        return anchors
    for scene in scenes:
        if isinstance(scene, dict):
            location = _normalize_storyboard_text(scene.get("location"), limit=120)
            atmosphere = _normalize_storyboard_text(scene.get("atmosphere"), limit=160)
            duration = _normalize_storyboard_text(scene.get("duration"), limit=40)
            if location or atmosphere:
                anchors.append({
                    "location": location,
                    "atmosphere": atmosphere,
                    "duration": duration or None,
                    "source": source,
                })
        else:
            text = _normalize_storyboard_text(scene, limit=220)
            if text:
                anchors.append({"location": text, "atmosphere": "", "duration": None, "source": source})
    return anchors


def _storyboard_page_brief(page: dict[str, Any], *, prefix: str) -> str:
    if not isinstance(page, dict):
        return ""
    page_number = page.get("page_number")
    panels = page.get("panels") if isinstance(page.get("panels"), list) else []
    scene = _normalize_storyboard_text(page.get("scene"), limit=120)
    turning_point = _normalize_storyboard_text(page.get("turning_point"), limit=120)
    page_goal = _normalize_storyboard_text(page.get("page_goal"), limit=120)
    must_keep = _extract_string_list(page.get("must_keep"), limit=80)[:3]

    lead_characters: list[str] = []
    for panel in panels[:3]:
        if isinstance(panel, dict):
            for name in panel.get("characters") or []:
                text = _normalize_storyboard_text(name, limit=40)
                if text and text not in lead_characters:
                    lead_characters.append(text)
                if len(lead_characters) >= 4:
                    break
        if len(lead_characters) >= 4:
            break

    return " | ".join(
        part
        for part in [
            f"{prefix} 第{page_number}页" if page_number is not None else prefix,
            f"页目标：{page_goal}" if page_goal else "",
            f"场景：{scene}" if scene else "",
            f"转折：{turning_point}" if turning_point else "",
            f"角色：{'、'.join(lead_characters)}" if lead_characters else "",
            f"必须保留：{'；'.join(must_keep)}" if must_keep else "",
        ]
        if part
    )


def _storyboard_bridge_brief(bridge_context: dict[str, Any]) -> str:
    if not isinstance(bridge_context, dict):
        return ""

    lines: list[str] = []
    for label, key in (("上一章", "previous_chapter"), ("下一章", "next_chapter")):
        entry = bridge_context.get(key)
        if not isinstance(entry, dict):
            continue

        header = f"- {label} 第{entry.get('chapter_number') or '未知'}章 {entry.get('chapter_title') or ''}".rstrip()
        lines.append(header)

        summary = _normalize_storyboard_text(entry.get("chapter_summary"), limit=220)
        if summary:
            lines.append(f"  - 章节摘要：{summary}")

        storyboard = entry.get("storyboard")
        if isinstance(storyboard, dict):
            opening_page = _normalize_storyboard_text(storyboard.get("opening_page"), limit=240)
            closing_page = _normalize_storyboard_text(storyboard.get("closing_page"), limit=240)
            if opening_page:
                lines.append(f"  - 开场页：{opening_page}")
            if closing_page:
                lines.append(f"  - 收束页：{closing_page}")

    return "\n".join(lines)


def _storyboard_bridge_summary(storyboard_pages: list[dict[str, Any]], *, chapter_number: int, chapter_title: str | None) -> dict[str, Any]:
    if not storyboard_pages:
        return {}
    first_page = storyboard_pages[0]
    last_page = storyboard_pages[-1]
    return {
        "chapter_number": chapter_number,
        "chapter_title": _normalize_storyboard_text(chapter_title, limit=120),
        "opening_page": _storyboard_page_brief(first_page, prefix="上一章开场"),
        "closing_page": _storyboard_page_brief(last_page, prefix="上一章收束"),
    }


def _character_continuity_score(
    character: Character,
    *,
    chapter_text: str,
    chapter_summary: str,
    focus_names: set[str],
    analysis_names: set[str],
    previous_analysis_names: set[str],
) -> int:
    score = 0
    if character.name in focus_names:
        score += 8
    if character.name in analysis_names:
        score += 10
    if character.name in previous_analysis_names:
        score += 6
    if character.name and character.name in chapter_text:
        score += 4
    if character.name and character.name in chapter_summary:
        score += 2
    if character.is_organization:
        score += 1
    if getattr(character, "status", None) and character.status != "active":
        score += 1
    return score


def _format_character_continuity_entry(character: Character) -> dict[str, Any]:
    return {
        "name": character.name,
        "type": "organization" if character.is_organization else "character",
        "role_type": character.role_type,
        "status": character.status or "active",
        "age": _normalize_storyboard_text(character.age, limit=30),
        "gender": _normalize_storyboard_text(character.gender, limit=30),
        "appearance": _normalize_storyboard_text(character.appearance, limit=220),
        "personality": _normalize_storyboard_text(character.personality, limit=180),
        "current_state": _normalize_storyboard_text(character.current_state, limit=120),
        "visual_bible": _extract_visual_bible_snapshot(character.visual_bible),
        "organization_type": _normalize_storyboard_text(character.organization_type, limit=80),
        "organization_purpose": _normalize_storyboard_text(character.organization_purpose, limit=140),
    }


async def _build_storyboard_continuity_context(
    *,
    project_id: str,
    chapter_number: int,
    db: AsyncSession,
    project: Project | None = None,
    chapter: Chapter | None = None,
) -> dict[str, Any]:
    if project is None:
        project_result = await db.execute(select(Project).where(Project.id == project_id))
        project = project_result.scalar_one_or_none()
    if chapter is None:
        chapter_result = await db.execute(
            select(Chapter).where(
                Chapter.project_id == project_id,
                Chapter.chapter_number == chapter_number,
            )
        )
        chapter = chapter_result.scalar_one_or_none()

    if project is None or chapter is None:
        raise RuntimeError("项目或章节不存在")

    storyboard_state = _read_json_file(_project_state_file(project_id, "storyboard"))
    storyboard_entries = _parse_int_keys(storyboard_state.get("scripted_chapters"), "scripted_chapters")

    current_analysis_result = await db.execute(
        select(PlotAnalysis)
        .join(Chapter, PlotAnalysis.chapter_id == Chapter.id)
        .where(Chapter.project_id == project_id, Chapter.chapter_number == chapter_number)
        .order_by(PlotAnalysis.created_at.desc())
        .limit(1)
    )
    current_analysis = current_analysis_result.scalar_one_or_none()

    previous_analysis = None
    if chapter_number > 1:
        previous_analysis_result = await db.execute(
            select(PlotAnalysis)
            .join(Chapter, PlotAnalysis.chapter_id == Chapter.id)
            .where(Chapter.project_id == project_id, Chapter.chapter_number == chapter_number - 1)
            .order_by(PlotAnalysis.created_at.desc())
            .limit(1)
        )
        previous_analysis = previous_analysis_result.scalar_one_or_none()

    expansion_plan = _load_jsonish(chapter.expansion_plan)
    if not isinstance(expansion_plan, dict):
        expansion_plan = {}

    focus_names = {
        _normalize_storyboard_text(name, limit=40)
        for name in _extract_string_list(expansion_plan.get("character_focus"), limit=40)
        if _normalize_storyboard_text(name, limit=40)
    }
    current_analysis_names = set()
    previous_analysis_names = set()
    if current_analysis and isinstance(current_analysis.character_states, list):
        current_analysis_names = {
            _normalize_storyboard_text(state.get("character_name"), limit=40)
            for state in current_analysis.character_states
            if isinstance(state, dict) and _normalize_storyboard_text(state.get("character_name"), limit=40)
        }
    if previous_analysis and isinstance(previous_analysis.character_states, list):
        previous_analysis_names = {
            _normalize_storyboard_text(state.get("character_name"), limit=40)
            for state in previous_analysis.character_states
            if isinstance(state, dict) and _normalize_storyboard_text(state.get("character_name"), limit=40)
        }

    bridge_context: dict[str, Any] = {}
    for offset, slot in ((-1, "previous_chapter"), (1, "next_chapter")):
        adjacent_number = chapter_number + offset
        if adjacent_number < 1:
            continue

        adjacent_result = await db.execute(
            select(Chapter).where(
                Chapter.project_id == project_id,
                Chapter.chapter_number == adjacent_number,
            )
        )
        adjacent_chapter = adjacent_result.scalar_one_or_none()
        if adjacent_chapter is None:
            continue

        adjacent_storyboard_artifact = await _get_storyboard_artifact(project_id, adjacent_number, db)
        adjacent_storyboard_payload = await _load_storyboard_content_preferred(
            _resolve_storyboard_for_chapter(project_id, adjacent_number),
            storyboard_entries.get(adjacent_number, {}),
            adjacent_storyboard_artifact,
        )
        adjacent_storyboard_pages = _extract_storyboard_pages(adjacent_storyboard_payload)
        bridge_entry: dict[str, Any] = {
            "chapter_number": adjacent_chapter.chapter_number,
            "chapter_title": _normalize_storyboard_text(adjacent_chapter.title, limit=120),
            "chapter_summary": _normalize_storyboard_text(adjacent_chapter.summary, limit=300),
        }
        bridge_summary = _storyboard_bridge_summary(
            adjacent_storyboard_pages,
            chapter_number=adjacent_number,
            chapter_title=adjacent_chapter.title,
        )
        if bridge_summary:
            bridge_entry["storyboard"] = bridge_summary
        bridge_context[slot] = bridge_entry

    chapter_text = chapter.content or ""
    chapter_summary = chapter.summary or ""

    characters_result = await db.execute(
        select(Character)
        .where(Character.project_id == project_id)
        .order_by(Character.created_at.asc())
    )
    all_characters = characters_result.scalars().all()
    if not all_characters:
        raise RuntimeError("项目暂无角色信息")

    scored_characters = sorted(
        all_characters,
        key=lambda character: (
            -_character_continuity_score(
                character,
                chapter_text=chapter_text,
                chapter_summary=chapter_summary,
                focus_names=focus_names,
                analysis_names=current_analysis_names,
                previous_analysis_names=previous_analysis_names,
            ),
            character.created_at or datetime.min,
            character.name,
        ),
    )
    selected_characters = scored_characters[:12]
    selected_names = {character.name for character in selected_characters}
    for name_set in (focus_names, current_analysis_names):
        for name in name_set:
            if name and name not in selected_names:
                matched = next((character for character in all_characters if character.name == name), None)
                if matched:
                    selected_characters.append(matched)
                    selected_names.add(name)

    selected_characters = sorted(
        {character.id: character for character in selected_characters}.values(),
        key=lambda character: (
            character.created_at or datetime.min,
            character.name,
        ),
    )

    if len(selected_characters) < 6:
        for character in scored_characters:
            if character.id not in {item.id for item in selected_characters}:
                selected_characters.append(character)
            if len(selected_characters) >= 6:
                break

    character_entries = [_format_character_continuity_entry(character) for character in selected_characters]
    scene_anchors = []
    scene_anchors.extend(_extract_analysis_scene_anchors(current_analysis, "current_analysis"))
    scene_anchors.extend(_extract_analysis_scene_anchors(previous_analysis, "previous_analysis"))
    expansion_scenes = expansion_plan.get("scenes")
    if isinstance(expansion_scenes, list):
        for scene in expansion_scenes:
            if isinstance(scene, dict):
                location = _normalize_storyboard_text(scene.get("location"), limit=120)
                atmosphere = _normalize_storyboard_text(scene.get("atmosphere"), limit=160)
                if location or atmosphere:
                    scene_anchors.append({
                        "location": location,
                        "atmosphere": atmosphere,
                        "duration": _normalize_storyboard_text(scene.get("duration"), limit=40) or None,
                        "source": "expansion_plan",
                    })
            else:
                text = _normalize_storyboard_text(scene, limit=220)
                if text:
                    scene_anchors.append({"location": text, "atmosphere": "", "duration": None, "source": "expansion_plan"})

    deduped_scene_anchors: list[dict[str, Any]] = []
    seen_scene_keys: set[tuple[str, str]] = set()
    for item in scene_anchors:
        key = (item.get("location") or "", item.get("atmosphere") or "")
        if key in seen_scene_keys:
            continue
        seen_scene_keys.add(key)
        deduped_scene_anchors.append(item)

    continuity_pack = {
        "project": {
            "title": _normalize_storyboard_text(project.title, limit=120),
            "genre": _normalize_storyboard_text(project.genre, limit=60),
            "theme": _normalize_storyboard_text(project.theme, limit=200),
            "world_time_period": _normalize_storyboard_text(project.world_time_period, limit=160),
            "world_location": _normalize_storyboard_text(project.world_location, limit=160),
            "world_atmosphere": _normalize_storyboard_text(project.world_atmosphere, limit=160),
            "world_rules": _normalize_storyboard_text(project.world_rules, limit=260),
            "comic_style": _project_comic_style_instruction(project),
        },
        "chapter": {
            "chapter_number": chapter.chapter_number,
            "title": _normalize_storyboard_text(chapter.title, limit=120),
            "summary": _normalize_storyboard_text(chapter.summary, limit=300),
            "expansion_goal": _normalize_storyboard_text(expansion_plan.get("goal"), limit=180),
            "emotional_tone": _normalize_storyboard_text(
                expansion_plan.get("emotional_tone") or getattr(current_analysis, "emotional_tone", None),
                limit=80,
            ),
            "plot_stage": _normalize_storyboard_text(getattr(current_analysis, "plot_stage", None), limit=40),
            "conflict_level": getattr(current_analysis, "conflict_level", None),
            "hooks": _extract_entry_texts(getattr(current_analysis, "hooks", None), keys=("content", "title", "position"), limit=160),
            "foreshadows": _extract_entry_texts(getattr(current_analysis, "foreshadows", None), keys=("content", "title", "type"), limit=160),
        },
        "continuity_rules": [
            "人物造型必须跟随 visual_bible 的 trigger_token 和 immutable_traits，除非章节正文明确说明造型变化。",
            "同一场景的空间结构、道具摆放、光线方向和镜头语言必须保持稳定。",
            "同一角色在同一章节内的发型、服装、五官比例和气质不能随意漂移。",
            "如果出现组织、据点或固定场景，后续页面必须沿用一致的识别元素。",
            "上一章的收束页与下一章的开场页属于高优先级连续性锚点，当前章的起承转合不能把它们打断。",
        ],
        "bridge_context": bridge_context,
        "characters": character_entries,
        "scene_anchors": deduped_scene_anchors[:8],
    }

    continuity_text = json.dumps(continuity_pack, ensure_ascii=False, indent=2)
    bridge_context_text = json.dumps(bridge_context, ensure_ascii=False, indent=2) if bridge_context else "暂无邻章桥接信息"
    selected_character_lines = []
    for character in selected_characters:
        entry = _format_character_continuity_entry(character)
        line_parts = [
            f"- {entry['name']}（{entry['type']}）",
            f"状态：{entry['status']}",
        ]
        if entry["appearance"]:
            line_parts.append(f"外貌锚点：{entry['appearance']}")
        if entry["current_state"]:
            line_parts.append(f"当前状态：{entry['current_state']}")
        if entry["visual_bible"].get("trigger_token"):
            line_parts.append(f"视觉触发词：{entry['visual_bible']['trigger_token']}")
        selected_character_lines.append(" | ".join(line_parts))

    characters_info = "\n".join(selected_character_lines) or "暂无角色信息"
    scene_brief_lines = [
        f"- {item.get('location') or '未命名场景'} | 氛围：{item.get('atmosphere') or '未设定'} | 来源：{item.get('source') or 'unknown'}"
        for item in deduped_scene_anchors[:6]
    ]
    bridge_brief = _storyboard_bridge_brief(bridge_context)
    page_continuity_brief = "\n".join(
        part
        for part in [
            f"项目：{continuity_pack['project']['title']} | {continuity_pack['project']['genre']} | {continuity_pack['project']['world_location'] or continuity_pack['project']['world_time_period'] or '未设定'}",
            f"章节：第{chapter.chapter_number}章 {continuity_pack['chapter']['title']}",
            f"章节摘要：{continuity_pack['chapter']['summary']}" if continuity_pack["chapter"]["summary"] else "",
            "桥接信息：",
            bridge_brief or "暂无邻章桥接信息",
            "角色锚点：",
            characters_info,
            "场景锚点：",
            "\n".join(scene_brief_lines) if scene_brief_lines else "暂无场景锚点",
            "连续性规则：",
            "\n".join(f"- {rule}" for rule in continuity_pack["continuity_rules"]),
        ]
        if part
    ).strip()

    return {
        "continuity_pack": continuity_text,
        "continuity_brief": page_continuity_brief,
        "bridge_context": bridge_context_text,
        "characters_info": characters_info,
        "character_entries": character_entries,
        "scene_anchors": deduped_scene_anchors[:8],
        "selected_character_names": [character.name for character in selected_characters],
    }


def _image_error_detail(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        response = exc.response
        body_preview = ""
        try:
            body_preview = response.text.strip()[:500]
        except Exception:
            body_preview = ""
        status_text = f"HTTP {response.status_code}"
        return f"{status_text}: {body_preview}" if body_preview else status_text
    return str(exc)


def _should_retry_comic_image_error(exc: Exception) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return _shared_should_retry_comic_image_error(exc.response.status_code, _image_error_detail(exc))
    return _shared_should_retry_comic_image_error(None, str(exc))


def _comic_image_retry_delay(attempt: int, exc: Exception) -> int:
    detail = _image_error_detail(exc).lower()
    if "429" in detail or "rate limit" in detail or "too many requests" in detail or "appchatreverse" in detail:
        return min(10 * attempt, 45)
    return min(3 * attempt, 15)


def _variant_matches_chapter(entry: dict[str, Any], chapter_number: int) -> bool:
    start = entry.get("chapter_start")
    end = entry.get("chapter_end")
    if start is None and end is None:
        return False
    if start is not None and chapter_number < int(start):
        return False
    if end is not None and chapter_number > int(end):
        return False
    return True


def _select_character_variant_for_chapter(variants: dict[str, Any], chapter_number: int) -> dict[str, Any] | None:
    normalized = [v for v in variants.values() if isinstance(v, dict)]
    if not normalized:
        return None
    matched = [v for v in normalized if v.get("variant_key") != DEFAULT_CHARACTER_IMAGE_VARIANT and _variant_matches_chapter(v, chapter_number)]
    if matched:
        matched.sort(key=lambda v: (
            int((v.get("chapter_end") or chapter_number) - (v.get("chapter_start") or chapter_number)),
            int(v.get("sort_order") or 0),
            str(v.get("variant_label") or ""),
        ))
        return matched[0]
    return next((v for v in normalized if v.get("variant_key") == DEFAULT_CHARACTER_IMAGE_VARIANT), normalized[0])


async def _build_character_image_references(project_id: str, chapter_number: int, db: AsyncSession) -> list[dict[str, Any]]:
    state = _read_character_image_state(project_id)
    character_result = await db.execute(
        select(Character)
        .where(Character.project_id == project_id, Character.is_organization.is_(False))
        .order_by(Character.created_at.asc())
    )
    artifact_result = await db.execute(
        select(CharacterImageArtifact).where(CharacterImageArtifact.project_id == project_id)
    )
    artifacts = {
        artifact.character_id: artifact
        for artifact in artifact_result.scalars().all()
    }
    refs: list[dict[str, Any]] = []
    raw_characters = state.get("characters") if isinstance(state.get("characters"), dict) else state
    for character in character_result.scalars().all():
        artifact = artifacts.get(character.id)
        raw_character_state = raw_characters.get(character.id) if isinstance(raw_characters, dict) else {}
        if isinstance(raw_character_state, dict) and isinstance(raw_character_state.get("variants"), dict):
            variants = raw_character_state["variants"]
        elif isinstance(raw_character_state, dict):
            variants = raw_character_state
        else:
            variants = {}
        selected = _select_character_variant_for_chapter(variants, chapter_number) or {}
        variant_key = selected.get("variant_key") or DEFAULT_CHARACTER_IMAGE_VARIANT
        image_url = selected.get("cdn_url") or selected.get("cos_url")
        if not image_url and selected.get("cos_object_key") and tencent_cos_storage.is_enabled():
            image_url = tencent_cos_storage.public_url(str(selected.get("cos_object_key")))
        if not image_url and artifact:
            image_url = artifact.cos_url
            if not image_url and artifact.cos_object_key and tencent_cos_storage.is_enabled():
                image_url = tencent_cos_storage.public_url(artifact.cos_object_key)
        has_image = bool(
            selected.get("has_image")
            or selected.get("cos_url")
            or selected.get("cdn_url")
            or selected.get("cos_object_key")
            or image_url
        )
        refs.append({
            "character_id": character.id,
            "name": character.name,
            "variant_key": variant_key,
            "variant_label": selected.get("variant_label") or "默认形象",
            "variant_type": selected.get("variant_type") or "default",
            "chapter_start": selected.get("chapter_start"),
            "chapter_end": selected.get("chapter_end"),
            "prompt": selected.get("prompt") or (artifact.prompt if artifact else "") or "",
            "has_image": has_image,
            "image_url": image_url,
        })
    return refs



class StoryboardUpdateRequest(BaseModel):
    markdown_content: str | None = Field(default=None)
    json_text: str | None = Field(default=None)
    json_content: Any | None = Field(default=None)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _safe_resolve(path: Path) -> Path:
    resolved = path.resolve(strict=False)
    try:
        resolved.relative_to(COMIC_STATE_ROOT)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="非法文件路径") from exc
    return resolved


def _safe_resolve_or_skip(path: Path) -> Path | None:
    try:
        return _safe_resolve(path)
    except HTTPException as exc:
        if exc.status_code == 400 and exc.detail == "非法文件路径":
            logger.warning("跳过非法漫画状态路径: %s", path)
            return None
        raise


def _project_root(project_id: str) -> Path:
    return _safe_resolve(COMIC_STATE_ROOT / project_id)


def _project_state_file(project_id: str, suffix: str) -> Path | None:
    candidates = [
        COMIC_STATE_ROOT / f"{project_id}_{suffix}_state.json",
        _project_root(project_id) / f"{suffix}_state.json",
    ]
    for candidate in candidates:
        safe_candidate = _safe_resolve(candidate)
        if safe_candidate.is_file():
            return safe_candidate
    return None


def _project_state_write_path(project_id: str, suffix: str) -> Path:
    existing = _project_state_file(project_id, suffix)
    if existing is not None:
        return existing
    return _safe_resolve(COMIC_STATE_ROOT / f"{project_id}_{suffix}_state.json")


def _state_lock(path: Path) -> RLock:
    safe_path = _safe_resolve(path)
    key = str(safe_path)
    with _STATE_LOCKS_GUARD:
        lock = _STATE_LOCKS.get(key)
        if lock is None:
            lock = RLock()
            _STATE_LOCKS[key] = lock
        return lock


def _read_json_file(path: Path | None) -> dict[str, Any]:
    if not path or not path.is_file():
        return {}
    try:
        with _state_lock(path):
            if not path.is_file():
                return {}
            data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("读取 JSON 失败: %s (%s)", path, exc)
        return {}
    return data if isinstance(data, dict) else {}


def _write_json_file(path: Path, data: dict[str, Any]) -> None:
    safe_path = _safe_resolve(path)
    with _state_lock(safe_path):
        safe_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = safe_path.with_name(f".{safe_path.name}.{uuid.uuid4().hex}.tmp")
        try:
            temp_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            temp_path.replace(safe_path)
        finally:
            try:
                if temp_path.exists():
                    temp_path.unlink()
            except OSError:
                logger.warning("清理临时状态文件失败: %s", temp_path, exc_info=True)


def _append_jsonl(path: Path, record: dict[str, Any]) -> None:
    safe_path = _safe_resolve(path)
    with _state_lock(safe_path):
        safe_path.parent.mkdir(parents=True, exist_ok=True)
        with safe_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def _iso_from_mtime(path: Path | None) -> str | None:
    if not path or not path.exists():
        return None
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _max_timestamp(*values: str | None) -> str | None:
    existing = [value for value in values if value]
    return max(existing) if existing else None


def _datetime_to_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.isoformat() + "Z"
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_json_loads(raw_text: str | None) -> Any:
    if not isinstance(raw_text, str) or not raw_text.strip():
        return None
    try:
        return json.loads(raw_text)
    except json.JSONDecodeError:
        return None


def _storyboard_artifact_exists(artifact: ComicStoryboardArtifact | None) -> bool:
    if artifact is None:
        return False
    return bool(
        artifact.json_text
        or artifact.markdown_content
        or artifact.json_local_path
        or artifact.markdown_local_path
        or artifact.json_cos_object_key
        or artifact.markdown_cos_object_key
    )


def _page_artifact_failed_metadata(artifact: ComicPageArtifact | None) -> dict[str, Any] | None:
    if artifact is None or not artifact.failed_metadata:
        return None
    parsed = _safe_json_loads(artifact.failed_metadata)
    return parsed if isinstance(parsed, dict) else None


def _page_has_image(page_path: Path | None, page_artifact: ComicPageArtifact | None) -> bool:
    return _shared_page_has_image(page_path, page_artifact)


async def _get_storyboard_artifact_map(project_id: str, db: AsyncSession) -> dict[int, ComicStoryboardArtifact]:
    result = await db.execute(
        select(ComicStoryboardArtifact).where(ComicStoryboardArtifact.project_id == project_id)
    )
    return {artifact.chapter_number: artifact for artifact in result.scalars().all()}


async def _get_page_artifact_map(project_id: str, db: AsyncSession) -> dict[tuple[int, int], ComicPageArtifact]:
    result = await db.execute(
        select(ComicPageArtifact).where(ComicPageArtifact.project_id == project_id)
    )
    return {(artifact.chapter_number, artifact.page_number): artifact for artifact in result.scalars().all()}


async def _get_storyboard_artifact(project_id: str, chapter_number: int, db: AsyncSession) -> ComicStoryboardArtifact | None:
    result = await db.execute(
        select(ComicStoryboardArtifact).where(
            ComicStoryboardArtifact.project_id == project_id,
            ComicStoryboardArtifact.chapter_number == chapter_number,
        )
    )
    return result.scalar_one_or_none()


async def _get_page_artifact(project_id: str, chapter_number: int, page_number: int, db: AsyncSession) -> ComicPageArtifact | None:
    result = await db.execute(
        select(ComicPageArtifact).where(
            ComicPageArtifact.project_id == project_id,
            ComicPageArtifact.chapter_number == chapter_number,
            ComicPageArtifact.page_number == page_number,
        )
    )
    return result.scalar_one_or_none()


def _parse_int_keys(mapping: dict[str, Any] | None, field_name: str) -> dict[int, dict[str, Any]]:
    parsed: dict[int, dict[str, Any]] = {}
    if not isinstance(mapping, dict):
        return parsed
    for key, value in mapping.items():
        try:
            parsed[int(key)] = value if isinstance(value, dict) else {}
        except (TypeError, ValueError):
            logger.warning("忽略非法 %s key: %s", field_name, key)
    return parsed


def _scan_storyboards(project_id: str) -> dict[int, dict[str, Any]]:
    storyboards_dir = _project_root(project_id) / "storyboards"
    safe_dir = _safe_resolve_or_skip(storyboards_dir)
    results: dict[int, dict[str, Any]] = {}
    if safe_dir is None or not safe_dir.is_dir():
        return results

    for entry in safe_dir.iterdir():
        if not entry.is_file():
            continue
        match = STORYBOARD_FILE_RE.match(entry.name)
        if not match:
            continue
        chapter_number = int(match.group(1))
        artifact_type = match.group(2)
        chapter_data = results.setdefault(chapter_number, {})
        safe_entry = _safe_resolve_or_skip(entry)
        if safe_entry is None:
            continue
        chapter_data[f"{artifact_type}_path"] = safe_entry
        chapter_data["updated_at"] = _max_timestamp(
            chapter_data.get("updated_at"),
            _iso_from_mtime(safe_entry),
        )
    return results


def _scan_manhua(project_id: str) -> dict[int, dict[str, Any]]:
    manhua_dir = _project_root(project_id) / "manhua"
    safe_dir = _safe_resolve_or_skip(manhua_dir)
    results: dict[int, dict[str, Any]] = {}
    if safe_dir is None or not safe_dir.is_dir():
        return results

    for entry in safe_dir.iterdir():
        if not entry.is_dir():
            continue
        match = CHAPTER_DIR_RE.match(entry.name)
        if not match:
            continue

        chapter_number = int(match.group(1))
        safe_entry = _safe_resolve_or_skip(entry)
        if safe_entry is None:
            continue
        chapter_data = results.setdefault(
            chapter_number,
            {"chapter_dir": safe_entry, "pages": {}, "prompts": {}, "updated_at": None},
        )
        for child in entry.iterdir():
            if not child.is_file():
                continue
            page_match = PAGE_FILE_RE.match(child.name)
            if page_match:
                page_number = int(page_match.group(1))
                safe_child = _safe_resolve_or_skip(child)
                if safe_child is None:
                    continue
                chapter_data["pages"][page_number] = safe_child
                chapter_data["updated_at"] = _max_timestamp(
                    chapter_data["updated_at"],
                    _iso_from_mtime(safe_child),
                )
                continue

            prompt_match = PROMPT_FILE_RE.match(child.name)
            if prompt_match:
                page_number = int(prompt_match.group(1))
                safe_child = _safe_resolve_or_skip(child)
                if safe_child is None:
                    continue
                chapter_data["prompts"][page_number] = safe_child
                chapter_data["updated_at"] = _max_timestamp(
                    chapter_data["updated_at"],
                    _iso_from_mtime(safe_child),
                )
    return results


def _load_regen_state(project_id: str) -> dict[str, Any]:
    state_file = _project_root(project_id) / REGEN_STATE_FILE
    state = _read_json_file(state_file)
    latest = state.get("latest_by_target")
    if not isinstance(latest, dict):
        state["latest_by_target"] = {}
    return state


def _save_regen_state(project_id: str, state: dict[str, Any]) -> None:
    state["project_id"] = project_id
    state["updated_at"] = _utc_now_iso()
    state.setdefault("latest_by_target", {})
    _write_json_file(_project_root(project_id) / REGEN_STATE_FILE, state)


def _load_comic_batch_state(project_id: str) -> dict[str, Any]:
    state = _read_json_file(_project_state_file(project_id, "comic_batch"))
    latest = state.get("latest_by_task")
    if not isinstance(latest, dict):
        state["latest_by_task"] = {}
    return state


def _save_comic_batch_state(project_id: str, state: dict[str, Any]) -> None:
    state["project_id"] = project_id
    state["updated_at"] = _utc_now_iso()
    state.setdefault("latest_by_task", {})
    _write_json_file(_project_state_write_path(project_id, "comic_batch"), state)


def _upsert_comic_batch_task(project_id: str, task: dict[str, Any]) -> None:
    state = _load_comic_batch_state(project_id)
    latest = state.setdefault("latest_by_task", {})
    latest[task["task_id"]] = task
    _save_comic_batch_state(project_id, state)


def _latest_comic_batch_task(project_id: str, task_id: str) -> dict[str, Any] | None:
    state = _load_comic_batch_state(project_id)
    latest = state.get("latest_by_task", {})
    task = latest.get(task_id) if isinstance(latest, dict) else None
    if not isinstance(task, dict):
        return None
    return _mark_orphaned_comic_batch_task(project_id, task)


def _mark_orphaned_batch_task(
    task: dict[str, Any],
    *,
    active_tasks: dict[str, dict[str, Any]],
    current_stage_key: str = "current_stage",
) -> bool:
    task_id = str(task.get("task_id") or "")
    if task.get("status") not in {"pending", "running"} or not task_id:
        return False
    if task_id in active_tasks:
        return False

    now = _utc_now_iso()
    task["status"] = "failed"
    task["error_message"] = ORPHANED_BATCH_TASK_MESSAGE
    task.setdefault("errors", [])
    if isinstance(task["errors"], list):
        already_recorded = any(
            isinstance(item, dict) and item.get("error") == ORPHANED_BATCH_TASK_MESSAGE
            for item in task["errors"]
        )
        if not already_recorded:
            task["errors"].append(
                {
                    "stage": task.get(current_stage_key) or "batch",
                    "chapter_number": task.get("current_chapter_number"),
                    "error": ORPHANED_BATCH_TASK_MESSAGE,
                }
            )
    task["interrupted_at"] = now
    task["completed_at"] = task.get("completed_at") or now
    task["updated_at"] = now
    return True


def _mark_orphaned_comic_batch_task(project_id: str, task: dict[str, Any]) -> dict[str, Any]:
    if _mark_orphaned_batch_task(task, active_tasks=_comic_batch_tasks):
        _upsert_comic_batch_task(project_id, task)
    return task


def _load_pipeline_batch_state(project_id: str) -> dict[str, Any]:
    state = _read_json_file(_project_state_file(project_id, "pipeline_batch"))
    latest = state.get("latest_by_task")
    if not isinstance(latest, dict):
        state["latest_by_task"] = {}
    return state


def _save_pipeline_batch_state(project_id: str, state: dict[str, Any]) -> None:
    state["project_id"] = project_id
    state["updated_at"] = _utc_now_iso()
    state.setdefault("latest_by_task", {})
    _write_json_file(_project_state_write_path(project_id, "pipeline_batch"), state)


def _upsert_pipeline_batch_task(project_id: str, task: dict[str, Any]) -> None:
    state = _load_pipeline_batch_state(project_id)
    latest = state.setdefault("latest_by_task", {})
    latest[task["task_id"]] = task
    _save_pipeline_batch_state(project_id, state)


def _latest_pipeline_batch_task(project_id: str, task_id: str) -> dict[str, Any] | None:
    state = _load_pipeline_batch_state(project_id)
    latest = state.get("latest_by_task", {})
    task = latest.get(task_id) if isinstance(latest, dict) else None
    if not isinstance(task, dict):
        return None
    return _mark_orphaned_pipeline_batch_task(project_id, task)


def _mark_orphaned_pipeline_batch_task(project_id: str, task: dict[str, Any]) -> dict[str, Any]:
    if _mark_orphaned_batch_task(task, active_tasks=_pipeline_batch_tasks):
        stages = task.get("stages")
        current_stage = task.get("current_stage")
        if isinstance(stages, dict) and current_stage in stages and isinstance(stages[current_stage], dict):
            stages[current_stage]["error_message"] = ORPHANED_BATCH_TASK_MESSAGE
        _upsert_pipeline_batch_task(project_id, task)
    return task


def _iter_project_ids_with_batch_state(suffix: str) -> set[str]:
    project_ids: set[str] = set()
    if not COMIC_STATE_ROOT.exists():
        return project_ids

    state_suffix = f"_{suffix}_state.json"
    for path in COMIC_STATE_ROOT.glob(f"*{state_suffix}"):
        if path.is_file() and path.name.endswith(state_suffix):
            project_id = path.name[: -len(state_suffix)]
            if project_id:
                project_ids.add(project_id)

    nested_file_name = f"{suffix}_state.json"
    for path in COMIC_STATE_ROOT.glob(f"*/{nested_file_name}"):
        if path.is_file() and path.parent.name:
            project_ids.add(path.parent.name)

    return project_ids


def mark_orphaned_batch_tasks_after_startup() -> dict[str, int]:
    """Mark persisted batch tasks left running by a previous process as failed."""
    summary = {
        "comic_projects": 0,
        "comic_tasks_marked": 0,
        "pipeline_projects": 0,
        "pipeline_tasks_marked": 0,
    }

    for project_id in _iter_project_ids_with_batch_state("comic_batch"):
        summary["comic_projects"] += 1
        state = _load_comic_batch_state(project_id)
        latest_by_task = state.get("latest_by_task")
        if not isinstance(latest_by_task, dict):
            continue
        for task in latest_by_task.values():
            if not isinstance(task, dict):
                continue
            before_status = task.get("status")
            _mark_orphaned_comic_batch_task(project_id, task)
            if before_status in {"pending", "running"} and task.get("status") == "failed":
                summary["comic_tasks_marked"] += 1

    for project_id in _iter_project_ids_with_batch_state("pipeline_batch"):
        summary["pipeline_projects"] += 1
        state = _load_pipeline_batch_state(project_id)
        latest_by_task = state.get("latest_by_task")
        if not isinstance(latest_by_task, dict):
            continue
        for task in latest_by_task.values():
            if not isinstance(task, dict):
                continue
            before_status = task.get("status")
            _mark_orphaned_pipeline_batch_task(project_id, task)
            if before_status in {"pending", "running"} and task.get("status") == "failed":
                summary["pipeline_tasks_marked"] += 1

    return summary


def _latest_active_pipeline_batch_task(project_id: str) -> dict[str, Any] | None:
    candidates: list[dict[str, Any]] = []
    for task in _pipeline_batch_tasks.values():
        if (
            isinstance(task, dict)
            and task.get("project_id") == project_id
            and task.get("status") in {"pending", "running"}
        ):
            candidates.append(task)

    state = _load_pipeline_batch_state(project_id)
    latest_by_task = state.get("latest_by_task")
    state_changed = False
    if isinstance(latest_by_task, dict):
        for task in latest_by_task.values():
            if isinstance(task, dict) and task.get("project_id") == project_id:
                before_status = task.get("status")
                _mark_orphaned_pipeline_batch_task(project_id, task)
                state_changed = state_changed or before_status != task.get("status")
            if (
                isinstance(task, dict)
                and task.get("project_id") == project_id
                and task.get("status") in {"pending", "running"}
            ):
                candidates.append(task)
    if state_changed:
        state = _load_pipeline_batch_state(project_id)
        latest_by_task = state.get("latest_by_task")
        if isinstance(latest_by_task, dict):
            candidates = [
                task
                for task in candidates
                if isinstance(task, dict) and task.get("status") in {"pending", "running"}
            ]

    if not candidates:
        return None

    deduped = {str(task.get("task_id")): task for task in candidates if task.get("task_id")}
    return sorted(
        deduped.values(),
        key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""),
        reverse=True,
    )[0]


_pipeline_batch_tasks: dict[str, dict[str, Any]] = {}
ComicFullPipelineGenerationMode = Literal["full", "incremental"]


def _pipeline_stage_state(total: int = 0) -> dict[str, Any]:
    return {
        "total": total,
        "processed": 0,
        "succeeded": 0,
        "failed": 0,
        "current_chapter_number": None,
        "current_retry_count": None,
        "error_message": None,
    }


def _pipeline_stage_states(total: int = 0, *, include_analysis: bool = False) -> dict[str, dict[str, Any]]:
    stages = {
        "chapter": _pipeline_stage_state(total),
        "storyboard": _pipeline_stage_state(total),
        "comic": _pipeline_stage_state(total),
    }
    if include_analysis:
        stages["analysis"] = _pipeline_stage_state(total)
    return stages


def _ensure_pipeline_stage_states(task: dict[str, Any], total: int, *, include_analysis: bool = False) -> None:
    stages = task.get("stages")
    if not isinstance(stages, dict):
        stages = {}
        task["stages"] = stages
    for key in ("chapter", "storyboard", "comic"):
        if not isinstance(stages.get(key), dict):
            stages[key] = _pipeline_stage_state(total)
    if include_analysis and not isinstance(stages.get("analysis"), dict):
        stages["analysis"] = _pipeline_stage_state(total)


def _chapter_has_content(chapter: Chapter | None) -> bool:
    return _shared_chapter_has_content(chapter)


def _chapter_pipeline_context_summary(chapter: Chapter | None) -> str | None:
    return _shared_chapter_pipeline_context_summary(chapter)


async def _get_chapter_analysis_state(chapter_id: str, db: AsyncSession) -> dict[str, Any]:
    analysis_result = await db.execute(
        select(PlotAnalysis)
        .where(PlotAnalysis.chapter_id == chapter_id)
        .order_by(PlotAnalysis.created_at.desc())
    )
    analysis = analysis_result.scalar_one_or_none()

    from app.models.analysis_task import AnalysisTask

    task_result = await db.execute(
        select(AnalysisTask)
        .where(AnalysisTask.chapter_id == chapter_id)
        .order_by(AnalysisTask.created_at.desc())
    )
    latest_task = task_result.scalars().first()
    latest_status = latest_task.status if latest_task else None
    return {
        "has_analysis": analysis is not None,
        "analysis_id": analysis.id if analysis else None,
        "has_active_task": latest_status in {"pending", "running"},
        "task_id": latest_task.id if latest_task else None,
        "task_status": latest_status,
    }


def _task_target_key(chapter_number: int, page_number: int) -> str:
    return f"chapter:{chapter_number}:page:{page_number}"


def _latest_task_for_page(regen_state: dict[str, Any], chapter_number: int, page_number: int) -> dict[str, Any] | None:
    latest = regen_state.get("latest_by_target", {})
    task = latest.get(_task_target_key(chapter_number, page_number))
    return task if isinstance(task, dict) else None


def _find_regen_task_by_id(regen_state: dict[str, Any], task_id: str) -> dict[str, Any] | None:
    latest = regen_state.get("latest_by_target", {})
    if not isinstance(latest, dict):
        return None
    for task in latest.values():
        if isinstance(task, dict) and task.get("task_id") == task_id:
            return task
    return None


def _extract_failed_pages(manhua_state: dict[str, Any]) -> dict[int, dict[int, dict[str, Any]]]:
    failed_pages: dict[int, dict[int, dict[str, Any]]] = {}
    raw_failed = manhua_state.get("failed_pages", {})
    if not isinstance(raw_failed, dict):
        return failed_pages

    for key, value in raw_failed.items():
        match = FAILED_PAGE_KEY_RE.match(str(key))
        if not match or not isinstance(value, dict):
            continue
        chapter_number = int(match.group("chapter"))
        page_number = int(match.group("page"))
        failed_pages.setdefault(chapter_number, {})[page_number] = value
    return failed_pages


def _drop_stale_failed_pages(
    chapter_failed_pages: dict[int, dict[str, Any]],
    chapter_pages: dict[int, Path],
) -> dict[int, dict[str, Any]]:
    return {
        page_number: failed_entry
        for page_number, failed_entry in chapter_failed_pages.items()
        if page_number not in chapter_pages
    }


def _storyboard_payload(
    storyboard_paths: dict[str, Any],
    storyboard_state: dict[str, Any],
    storyboard_artifact: ComicStoryboardArtifact | None = None,
) -> dict[str, Any]:
    json_path = storyboard_paths.get("json_path")
    md_path = storyboard_paths.get("md_path")
    updated_at = _max_timestamp(
        storyboard_paths.get("updated_at"),
        storyboard_state.get("updated_at"),
        _datetime_to_iso(storyboard_artifact.updated_at) if storyboard_artifact else None,
    )
    artifact_exists = _storyboard_artifact_exists(storyboard_artifact)
    return {
        "exists": bool(json_path or md_path or artifact_exists),
        "status": (storyboard_artifact.status if storyboard_artifact else None) or storyboard_state.get("status") or ("available" if json_path or md_path or artifact_exists else "missing"),
        "json_path": str(json_path) if isinstance(json_path, Path) else (storyboard_artifact.json_local_path if storyboard_artifact else None),
        "markdown_path": str(md_path) if isinstance(md_path, Path) else (storyboard_artifact.markdown_local_path if storyboard_artifact else None),
        "updated_at": updated_at,
        "mtime": updated_at,
    }


def _load_storyboard_content(
    storyboard_paths: dict[str, Any],
    storyboard_state: dict[str, Any] | None = None,
    storyboard_artifact: ComicStoryboardArtifact | None = None,
) -> dict[str, Any]:
    storyboard_state = storyboard_state or {}
    json_path = storyboard_paths.get("json_path")
    md_path = storyboard_paths.get("md_path")

    json_text = storyboard_artifact.json_text if storyboard_artifact and storyboard_artifact.json_text else None
    json_content = _safe_json_loads(json_text)
    if json_text is None and isinstance(json_path, Path) and json_path.is_file():
        json_text = json_path.read_text(encoding="utf-8")
        try:
            json_content = json.loads(json_text)
        except json.JSONDecodeError:
            logger.warning("分镜 JSON 解析失败: %s", json_path)

    markdown_content = storyboard_artifact.markdown_content if storyboard_artifact and storyboard_artifact.markdown_content else None
    if markdown_content is None and isinstance(md_path, Path) and md_path.is_file():
        markdown_content = md_path.read_text(encoding="utf-8")

    payload = _storyboard_payload(storyboard_paths, storyboard_state, storyboard_artifact)
    payload.update(
        {
            "json_text": json_text,
            "json_content": json_content,
            "markdown_content": markdown_content,
        }
    )
    return payload


async def _load_storyboard_content_preferred(
    storyboard_paths: dict[str, Any],
    storyboard_state: dict[str, Any] | None = None,
    storyboard_artifact: ComicStoryboardArtifact | None = None,
) -> dict[str, Any]:
    payload = _load_storyboard_content(storyboard_paths, storyboard_state, storyboard_artifact)

    if payload.get("json_text") is None and storyboard_artifact and storyboard_artifact.json_cos_object_key and tencent_cos_storage.is_enabled():
        try:
            content, _ = await tencent_cos_storage.download_bytes(object_key=storyboard_artifact.json_cos_object_key)
            payload["json_text"] = content.decode("utf-8")
            payload["json_content"] = _safe_json_loads(payload["json_text"])
        except Exception:
            logger.warning("从 COS 读取分镜 JSON 失败，回退本地文件: project_id=%s chapter=%s", storyboard_artifact.project_id, storyboard_artifact.chapter_number, exc_info=True)

    if payload.get("markdown_content") is None and storyboard_artifact and storyboard_artifact.markdown_cos_object_key and tencent_cos_storage.is_enabled():
        try:
            content, _ = await tencent_cos_storage.download_bytes(object_key=storyboard_artifact.markdown_cos_object_key)
            payload["markdown_content"] = content.decode("utf-8")
        except Exception:
            logger.warning("从 COS 读取分镜 Markdown 失败，回退本地文件: project_id=%s chapter=%s", storyboard_artifact.project_id, storyboard_artifact.chapter_number, exc_info=True)

    return payload


def _storyboard_page_panel_counts(json_content: Any, markdown_content: str | None) -> tuple[int | None, int | None]:
    if isinstance(json_content, dict):
        raw_pages = json_content.get("pages")
        if isinstance(raw_pages, list):
            page_count = len(raw_pages)
            panel_count = 0
            for page in raw_pages:
                if isinstance(page, dict) and isinstance(page.get("panels"), list):
                    panel_count += len(page["panels"])
            return page_count, panel_count

    if isinstance(markdown_content, str) and markdown_content.strip():
        page_count = len(re.findall(r"^###\s*第\s*\d+\s*页", markdown_content, flags=re.MULTILINE))
        panel_count = len(re.findall(r"^- \*\*镜\s*\d+", markdown_content, flags=re.MULTILINE))
        return (page_count or None), (panel_count or None)

    return None, None


def _normalize_storyboard_json(
    json_text: str | None,
    json_content: Any,
) -> tuple[str | None, Any]:
    if json_content is not None:
        normalized_text = json.dumps(json_content, ensure_ascii=False, indent=2)
        return normalized_text, json_content

    if json_text is not None:
        parsed_content = json.loads(json_text)
        normalized_text = json.dumps(parsed_content, ensure_ascii=False, indent=2)
        return normalized_text, parsed_content

    return None, None


def _merge_cos_metadata(prefix: str, artifact: ComicStoryboardArtifact | ComicPageArtifact, metadata: COSObjectMetadata | None) -> None:
    if metadata is None:
        return
    setattr(artifact, f"{prefix}_cos_bucket", metadata.bucket)
    setattr(artifact, f"{prefix}_cos_region", metadata.region)
    setattr(artifact, f"{prefix}_cos_object_key", metadata.object_key)
    setattr(artifact, f"{prefix}_cos_url", metadata.url)
    setattr(artifact, f"{prefix}_cos_etag", metadata.etag)
    setattr(artifact, f"{prefix}_content_length", metadata.content_length)


async def _upload_storyboard_artifact(
    *,
    project_id: str,
    chapter_number: int,
    suffix: str,
    content: bytes,
    content_type: str,
) -> COSObjectMetadata | None:
    if not tencent_cos_storage.is_enabled():
        return None
    object_key = tencent_cos_storage.build_object_key(
        "comics",
        project_id,
        "storyboards",
        f"chapter_{chapter_number:04d}_storyboard.{suffix}",
    )
    return await tencent_cos_storage.upload_bytes(
        object_key=object_key,
        content=content,
        content_type=content_type,
    )


async def _upload_comic_page_artifact(
    *,
    project_id: str,
    chapter_number: int,
    page_file_name: str,
    content: bytes,
    content_type: str,
) -> COSObjectMetadata | None:
    _require_cos_image_storage()
    object_key = tencent_cos_storage.build_object_key(
        "comics",
        project_id,
        "pages",
        f"chapter_{chapter_number:04d}",
        page_file_name,
    )
    return await tencent_cos_storage.upload_bytes(
        object_key=object_key,
        content=content,
        content_type=content_type,
    )


def _page_status(
    page_path: Path | None,
    failed_entry: dict[str, Any] | None,
    regen_task: dict[str, Any] | None,
    page_artifact: ComicPageArtifact | None,
) -> str:
    if regen_task and regen_task.get("status") in {"queued", "running", "failed", "completed"}:
        return str(regen_task["status"])
    if _page_has_image(page_path, page_artifact):
        return "ready"
    if page_artifact and page_artifact.status and page_artifact.status != "missing":
        if page_artifact.status == "ready":
            return "missing"
        return page_artifact.status
    artifact_failed = _page_artifact_failed_metadata(page_artifact)
    if artifact_failed:
        return "failed"
    if failed_entry:
        return "failed"
    return "missing"


def _page_error_message(
    failed_entry: dict[str, Any] | None,
    regen_task: dict[str, Any] | None,
    page_artifact: ComicPageArtifact | None,
) -> str | None:
    if regen_task and regen_task.get("error_message"):
        return str(regen_task["error_message"])
    if regen_task and regen_task.get("worker_error"):
        return str(regen_task["worker_error"])
    if page_artifact and page_artifact.error_message:
        return page_artifact.error_message
    if not failed_entry:
        return None

    parts = [str(failed_entry.get("category") or "").strip(), str(failed_entry.get("response_excerpt") or "").strip()]
    message = " ".join(part for part in parts if part)
    return message or None


def _page_image_url(
    project_id: str,
    chapter_number: int,
    page_number: int,
    page_artifact: ComicPageArtifact | None,
    image_available: bool,
) -> str | None:
    if not image_available:
        return None
    if page_artifact and page_artifact.image_cos_url:
        return page_artifact.image_cos_url
    if page_artifact and page_artifact.image_cos_object_key and tencent_cos_storage.is_enabled():
        return tencent_cos_storage.public_url(page_artifact.image_cos_object_key)
    return None


def _build_page_payload(
    project_id: str,
    chapter_number: int,
    page_number: int,
    page_path: Path | None,
    prompt_path: Path | None,
    failed_entry: dict[str, Any] | None,
    regen_task: dict[str, Any] | None,
    page_artifact: ComicPageArtifact | None = None,
) -> dict[str, Any]:
    artifact_failed = _page_artifact_failed_metadata(page_artifact)
    effective_failed_entry = failed_entry or artifact_failed
    effective_prompt_path = prompt_path
    if effective_prompt_path is None and page_artifact and page_artifact.prompt_local_path:
        effective_prompt_path = Path(page_artifact.prompt_local_path)
    image_available = _page_has_image(None, page_artifact)
    updated_at = _max_timestamp(
        _iso_from_mtime(effective_prompt_path),
        effective_failed_entry.get("updated_at") if effective_failed_entry else None,
        regen_task.get("updated_at") if regen_task else None,
        _datetime_to_iso(page_artifact.updated_at) if page_artifact else None,
    )
    return {
        "page_number": page_number,
        "status": _page_status(None, effective_failed_entry, regen_task, page_artifact),
        "image_available": image_available,
        "image_url": _page_image_url(project_id, chapter_number, page_number, page_artifact, image_available),
        "prompt_path": str(effective_prompt_path) if effective_prompt_path else (page_artifact.prompt_cos_url if page_artifact else None),
        "file_path": page_artifact.image_cos_url if page_artifact and page_artifact.image_cos_url else None,
        "failed": effective_failed_entry is not None,
        "failed_metadata": effective_failed_entry or None,
        "regeneration": regen_task,
        "error_message": _page_error_message(effective_failed_entry, regen_task, page_artifact),
        "updated_at": updated_at,
        "mtime": updated_at,
    }


def _extract_storyboard_pages(storyboard_payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(storyboard_payload, dict):
        return []
    json_content = storyboard_payload.get("json_content")
    if not isinstance(json_content, dict):
        return []
    pages = json_content.get("pages")
    if not isinstance(pages, list):
        return []
    return [page for page in pages if isinstance(page, dict)]


def _storyboard_page_map(storyboard_pages: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    page_map: dict[int, dict[str, Any]] = {}
    for page in storyboard_pages:
        try:
            page_number = int(page.get("page_number"))
        except (TypeError, ValueError):
            continue
        page_map[page_number] = page
    return page_map


def _storyboard_panel_prompt(panel: dict[str, Any], *, image_text_language: str | None = None) -> str:
    parts: list[str] = []
    panel_number = panel.get("panel_number")
    if panel_number is not None:
        parts.append(f"第{panel_number}格")
    description = _soften_sensitive_comic_text(str(panel.get("description") or "").strip())
    if description:
        parts.append(f"画面：{description}")
    scene = _soften_sensitive_comic_text(str(panel.get("scene") or "").strip())
    if scene:
        parts.append(f"场景：{scene}")
    characters = panel.get("characters")
    if isinstance(characters, list) and characters:
        names = "、".join(str(item).strip() for item in characters if str(item).strip())
        if names:
            parts.append(f"角色：{names}")
    camera_angle = str(panel.get("camera_angle") or "").strip()
    if camera_angle:
        parts.append(f"镜头：{camera_angle}")
    emotion = _soften_sensitive_comic_text(str(panel.get("emotion") or "").strip())
    if emotion:
        parts.append(f"氛围：{emotion}")
    dialogue = _soften_sensitive_comic_text(str(panel.get("dialogue") or "").strip(), aggressive=True)
    if dialogue:
        if _contains_cjk_text(dialogue) or normalize_image_text_language(image_text_language) == "zh":
            parts.append(build_dialogue_reference(dialogue, image_text_language))
        else:
            parts.append(f"Dialogue reference: {dialogue}")
    return "；".join(parts)


def _storyboard_page_window_brief(storyboard_pages_by_number: dict[int, dict[str, Any]], page_number: int) -> str:
    lines: list[str] = []
    for offset, label in ((-1, "上一页"), (0, "当前页"), (1, "下一页")):
        page = storyboard_pages_by_number.get(page_number + offset)
        if not isinstance(page, dict):
            continue
        brief = _storyboard_page_brief(page, prefix=label)
        if brief:
            lines.append(f"- {brief}")
    return "\n".join(lines)


def _storyboard_prompt_metadata_path(prompt_path: Path) -> Path:
    return _shared_storyboard_prompt_metadata_path(prompt_path)


def _storyboard_prompt_context_hash(
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
    image_text_language: str | None = None,
) -> str:
    return _shared_storyboard_prompt_context_hash(
        project_id=project_id,
        chapter_number=chapter_number,
        page_number=page_number,
        total_pages=total_pages,
        page_data=page_data,
        continuity_pack=continuity_pack,
        page_context=page_context,
        character_reference_brief=character_reference_brief,
        comic_style_instruction=comic_style_instruction,
        image_text_language=image_text_language,
    )


def _load_storyboard_prompt_metadata(prompt_path: Path) -> dict[str, Any] | None:
    return _shared_load_storyboard_prompt_metadata(prompt_path)


def _write_storyboard_prompt_metadata(prompt_path: Path, metadata: dict[str, Any]) -> None:
    _shared_write_storyboard_prompt_metadata(prompt_path, metadata)


def _normalize_comic_edit_input_image_bytes(
    image_bytes: bytes,
    *,
    error_prefix: str = "分镜改图输入图片校验失败",
) -> bytes:
    normalized_bytes, source_format, image_issue = normalize_image_bytes_to_png(image_bytes)
    if image_issue:
        raise RuntimeError(f"{error_prefix}: {image_issue}")
    if source_format and source_format != "png":
        logger.info(
            "漫画页图片已归一化为 PNG: source_format=%s bytes=%s",
            source_format,
            len(normalized_bytes),
        )
    return normalized_bytes


def _storyboard_prompt_request_summary(
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
    return _shared_storyboard_prompt_request_summary(
        project_title=project_title,
        chapter_number=chapter_number,
        chapter_title=chapter_title,
        page_number=page_number,
        page_data=page_data,
        page_context=page_context,
        character_reference_brief=character_reference_brief,
        comic_style_instruction=comic_style_instruction,
    )


def _character_image_reference_brief(
    references: list[dict[str, Any]] | None,
    *,
    page_data: dict[str, Any] | None = None,
) -> str:
    return StoryboardWorkflowAgent.build_character_reference_brief(references, page_data=page_data)


def _build_storyboard_page_prompt(
    *,
    project_title: str | None,
    chapter_number: int,
    chapter_title: str | None,
    page_number: int,
    page_data: dict[str, Any],
    total_pages: int | None = None,
    continuity_pack: str | None = None,
    page_context: str | None = None,
    character_reference_brief: str | None = None,
    comic_style_instruction: str | None = None,
    image_text_language: str | None = None,
) -> str:
    project_name = project_title or "当前小说"
    resolved_chapter_title = chapter_title or f"第{chapter_number}章"
    page_header = f"第{page_number}页"
    if total_pages:
        page_header = f"第{page_number}页 / 共{total_pages}页"

    panels = page_data.get("panels") or []
    panel_lines = [
        _storyboard_panel_prompt(panel, image_text_language=image_text_language)
        for panel in panels
        if isinstance(panel, dict)
    ]

    prompt_parts = [
        f"为小说《{project_name}》第{chapter_number}章《{resolved_chapter_title}》生成一张竖版国漫/漫画页面。",
        f"页面：{page_header}",
        f"统一视觉风格：{comic_style_instruction}" if comic_style_instruction else "",
        f"连续性设定：{continuity_pack}" if continuity_pack else "",
        f"页间连续性窗口：\n{page_context}" if page_context else "",
        f"角色视觉参考：\n{character_reference_brief}" if character_reference_brief else "",
        "生成要求：保持本章内角色外貌、服装、发型和气质一致；构图为完整漫画页面；竖版 720x1280；不要水印、logo 或边框。",
        "一致性规则：命名角色出现时，必须严格匹配角色视觉参考；除非分镜明确说明变化，否则相邻页面要保持相同脸型、发型、服装轮廓、色彩、道具和场景地理关系。",
        build_visible_text_rule(image_text_language, scope="comic"),
        SENSITIVE_COMIC_PROMPT_RULE,
        "重点：场景变化清晰，角色动作明确，镜头层次充足，情绪氛围鲜明，画面细节丰富。",
        "本页分镜：",
        *[f"- {line}" for line in panel_lines if line],
    ]
    return "\n".join(prompt_parts).strip()


def _ensure_storyboard_page_prompt_file(
    *,
    project_id: str,
    project_title: str | None,
    chapter_number: int,
    chapter_title: str | None,
    page_number: int,
    page_data: dict[str, Any],
    total_pages: int | None = None,
    continuity_pack: str | None = None,
    page_context: str | None = None,
    character_reference_brief: str | None = None,
    comic_style_instruction: str | None = None,
    image_text_language: str | None = None,
) -> Path:
    chapter_dir = _project_root(project_id) / "manhua" / f"chapter_{chapter_number:04d}"
    chapter_dir.mkdir(parents=True, exist_ok=True)
    prompt_path = _safe_resolve(chapter_dir / f"page_{page_number:02d}_prompt.txt")
    prompt_hash = _storyboard_prompt_context_hash(
        project_id=project_id,
        chapter_number=chapter_number,
        page_number=page_number,
        total_pages=total_pages,
        page_data=page_data,
        continuity_pack=continuity_pack,
        page_context=page_context,
        character_reference_brief=character_reference_brief,
        comic_style_instruction=comic_style_instruction,
        image_text_language=image_text_language,
    )
    existing_metadata = _load_storyboard_prompt_metadata(prompt_path) if prompt_path.is_file() else None
    if _shared_is_storyboard_prompt_fresh(existing_metadata, context_hash=prompt_hash, prompt_version=6):
        return prompt_path

    prompt_text = _build_storyboard_page_prompt(
        project_title=project_title,
        chapter_number=chapter_number,
        chapter_title=chapter_title,
        page_number=page_number,
        page_data=page_data,
        total_pages=total_pages,
        continuity_pack=continuity_pack,
        page_context=page_context,
        character_reference_brief=character_reference_brief,
        comic_style_instruction=comic_style_instruction,
        image_text_language=image_text_language,
    )
    prompt_path.write_text(prompt_text + "\n", encoding="utf-8")
    _write_storyboard_prompt_metadata(
        prompt_path,
        {
            "prompt_version": 6,
            "context_hash": prompt_hash,
            "updated_at": _utc_now_iso(),
            "request_summary": _storyboard_prompt_request_summary(
                project_title=project_title,
                chapter_number=chapter_number,
                chapter_title=chapter_title,
                page_number=page_number,
                page_data=page_data,
                page_context=page_context,
                character_reference_brief=character_reference_brief,
                comic_style_instruction=comic_style_instruction,
            ),
        },
    )
    return prompt_path


def _discover_chapter_page_numbers(
    generated_entry: dict[str, Any],
    manhua_scan: dict[str, Any],
    chapter_failed_pages: dict[int, dict[str, Any]],
    page_artifacts: dict[tuple[int, int], ComicPageArtifact],
    chapter_number: int,
    storyboard_pages: list[dict[str, Any]] | None = None,
) -> list[int]:
    page_numbers: set[int] = set()

    raw_generated_pages = generated_entry.get("pages")
    if isinstance(raw_generated_pages, list):
        for page in raw_generated_pages:
            if isinstance(page, int):
                page_numbers.add(page)

    page_numbers.update(manhua_scan.get("pages", {}).keys())
    page_numbers.update(manhua_scan.get("prompts", {}).keys())
    page_numbers.update(chapter_failed_pages.keys())
    page_numbers.update(page for artifact_chapter, page in page_artifacts.keys() if artifact_chapter == chapter_number)
    if storyboard_pages:
        page_numbers.update(_storyboard_page_map(storyboard_pages).keys())
    return sorted(page_numbers)


def _filter_missing_comic_page_numbers(
    page_numbers: list[int],
    manhua_scan: dict[str, Any],
    page_artifacts: dict[tuple[int, int], ComicPageArtifact],
    chapter_number: int,
) -> list[int]:
    return _shared_filter_missing_comic_page_numbers(page_numbers, manhua_scan, page_artifacts, chapter_number)


def _build_chapter_listing_payload(
    project_id: str,
    chapter_number: int,
    chapter_map: dict[int, Chapter],
    storyboard_entries: dict[int, dict[str, Any]],
    manhua_entries: dict[int, dict[str, Any]],
    failed_pages: dict[int, dict[int, dict[str, Any]]],
    storyboard_scan: dict[int, dict[str, Any]],
    manhua_scan: dict[int, dict[str, Any]],
    regen_state: dict[str, Any],
    storyboard_artifacts: dict[int, ComicStoryboardArtifact],
    page_artifacts: dict[tuple[int, int], ComicPageArtifact],
) -> dict[str, Any]:
    chapter = chapter_map.get(chapter_number)
    storyboard_entry = storyboard_entries.get(chapter_number, {})
    generated_entry = manhua_entries.get(chapter_number, {})
    storyboard_paths = storyboard_scan.get(chapter_number, {})
    storyboard_artifact = storyboard_artifacts.get(chapter_number)
    storyboard_content = _load_storyboard_content(storyboard_paths, storyboard_entry, storyboard_artifact)
    storyboard_pages = _extract_storyboard_pages(storyboard_content)
    chapter_manhua = manhua_scan.get(chapter_number, {})
    chapter_failed_pages = _drop_stale_failed_pages(
        failed_pages.get(chapter_number, {}),
        chapter_manhua.get("pages", {}),
    )
    page_numbers = _discover_chapter_page_numbers(
        generated_entry,
        chapter_manhua,
        chapter_failed_pages,
        page_artifacts,
        chapter_number,
        storyboard_pages,
    )

    pages = [
        _build_page_payload(
            project_id=project_id,
            chapter_number=chapter_number,
            page_number=page_number,
            page_path=chapter_manhua.get("pages", {}).get(page_number),
            prompt_path=chapter_manhua.get("prompts", {}).get(page_number),
            failed_entry=chapter_failed_pages.get(page_number),
            regen_task=_latest_task_for_page(regen_state, chapter_number, page_number),
            page_artifact=page_artifacts.get((chapter_number, page_number)),
        )
        for page_number in page_numbers
    ]
    chapter_updated_at = _max_timestamp(
        _datetime_to_iso(chapter.updated_at) if chapter else None,
        chapter_manhua.get("updated_at"),
        storyboard_paths.get("updated_at"),
        storyboard_entry.get("updated_at"),
        generated_entry.get("updated_at"),
        _datetime_to_iso(storyboard_artifact.updated_at) if storyboard_artifact else None,
        _max_timestamp(
            *[
                _datetime_to_iso(page_artifacts[(chapter_number, page_number)].updated_at)
                for page_number in page_numbers
                if (chapter_number, page_number) in page_artifacts
            ]
        ),
    )
    return {
        "chapter_number": chapter_number,
        "chapter_id": chapter.id if chapter else storyboard_entry.get("chapter_id") or generated_entry.get("chapter_id"),
        "chapter_title": _chapter_title(chapter, storyboard_entry, generated_entry),
        "chapter_status": _chapter_status(generated_entry, pages),
        "storyboard": _storyboard_payload(storyboard_paths, storyboard_entry, storyboard_artifact),
        "page_count": len(pages),
        "available_page_count": sum(1 for page in pages if page["image_available"]),
        "pages": pages,
        "failed_page_numbers": sorted(chapter_failed_pages.keys()),
        "updated_at": chapter_updated_at,
        "mtime": chapter_updated_at,
    }


def _build_chapter_regeneration_status_payload(
    project_id: str,
    chapter_listing: dict[str, Any],
) -> dict[str, Any]:
    pages = chapter_listing.get("pages", [])
    queued_page_count = sum(1 for page in pages if page.get("status") == "queued")
    running_page_count = sum(1 for page in pages if page.get("status") == "running")
    failed_page_count = sum(1 for page in pages if page.get("status") == "failed")
    completed_page_count = sum(
        1 for page in pages if page.get("status") in {"ready", "completed"}
    )
    return {
        "project_id": project_id,
        "chapter_number": chapter_listing["chapter_number"],
        "chapter_id": chapter_listing.get("chapter_id"),
        "chapter_title": chapter_listing.get("chapter_title"),
        "chapter_status": chapter_listing.get("chapter_status"),
        "page_count": chapter_listing.get("page_count", 0),
        "available_page_count": chapter_listing.get("available_page_count", 0),
        "queued_page_count": queued_page_count,
        "running_page_count": running_page_count,
        "failed_page_count": failed_page_count,
        "completed_page_count": completed_page_count,
        "pages": pages,
        "updated_at": chapter_listing.get("updated_at"),
        "mtime": chapter_listing.get("mtime"),
    }


def _build_continuous_reading_payload(
    project_id: str,
    chapters_payload: list[dict[str, Any]],
) -> dict[str, Any]:
    reading_chapters: list[dict[str, Any]] = []
    reading_pages: list[dict[str, Any]] = []
    updated_values: list[str] = []

    for chapter in sorted(chapters_payload, key=lambda item: item["chapter_number"]):
        available_pages = [
            {
                **page,
                "chapter_number": chapter["chapter_number"],
                "chapter_title": chapter.get("chapter_title"),
            }
            for page in chapter.get("pages", [])
            if page.get("image_available")
        ]
        if not available_pages:
            continue

        chapter_updated_at = _max_timestamp(
            chapter.get("updated_at"),
            *[page.get("updated_at") for page in available_pages],
        )
        reading_chapters.append(
            {
                "chapter_number": chapter["chapter_number"],
                "chapter_id": chapter.get("chapter_id"),
                "chapter_title": chapter.get("chapter_title"),
                "chapter_status": chapter.get("chapter_status"),
                "page_count": len(available_pages),
                "updated_at": chapter_updated_at,
                "pages": available_pages,
            }
        )
        reading_pages.extend(available_pages)
        if chapter_updated_at:
            updated_values.append(chapter_updated_at)

    return {
        "project_id": project_id,
        "chapters": reading_chapters,
        "pages": reading_pages,
        "summary": {
            "chapter_count": len(reading_chapters),
            "page_count": len(reading_pages),
        },
        "updated_at": _max_timestamp(*updated_values),
    }


def _chapter_detail_payload(chapter: Chapter | None, chapter_number: int, fallback_title: str | None = None) -> dict[str, Any]:
    return {
        "id": chapter.id if chapter else None,
        "number": chapter.chapter_number if chapter else chapter_number,
        "title": chapter.title if chapter else fallback_title,
        "content": chapter.content if chapter else None,
        "summary": chapter.summary if chapter else None,
        "status": chapter.status if chapter else None,
        "word_count": chapter.word_count if chapter else None,
        "updated_at": _datetime_to_iso(chapter.updated_at) if chapter else None,
    }


async def _get_project_chapter_map(project_id: str, db: AsyncSession) -> dict[int, Chapter]:
    result = await db.execute(
        select(Chapter)
        .where(Chapter.project_id == project_id)
        .order_by(Chapter.chapter_number)
    )
    chapters = result.scalars().all()
    return {chapter.chapter_number: chapter for chapter in chapters}


def _chapter_title(chapter: Chapter | None, storyboard_entry: dict[str, Any], manhua_entry: dict[str, Any]) -> str | None:
    return (
        (chapter.title if chapter else None)
        or storyboard_entry.get("chapter_title")
        or manhua_entry.get("chapter_title")
    )


def _chapter_status(generated_entry: dict[str, Any], pages: list[dict[str, Any]]) -> str:
    task_statuses = {page.get("status") for page in pages}
    if "running" in task_statuses:
        return "running"
    if "queued" in task_statuses:
        return "queued"

    has_failed = any(page.get("failed") or page.get("status") == "failed" for page in pages)
    available_count = sum(1 for page in pages if page.get("image_available"))
    if has_failed and available_count > 0:
        return "partial"
    if has_failed:
        return "failed"
    if pages and 0 < available_count < len(pages):
        return "partial"
    if pages and available_count == len(pages):
        return "completed"

    generated_status = generated_entry.get("status")
    if isinstance(generated_status, str) and generated_status:
        return generated_status
    return "missing"


async def _build_regen_task(
    project_id: str,
    user_id: str,
    chapter_number: int,
    page_number: int,
    chapter: Chapter | None,
    page_path: Path | None,
    prompt_path: Path | None,
    db: AsyncSession,
) -> dict[str, Any]:
    now = _utc_now_iso()
    character_image_references = await _build_character_image_references(project_id, chapter_number, db)
    prompt_metadata = _load_storyboard_prompt_metadata(Path(prompt_path)) if prompt_path else None
    return {
        "task_id": str(uuid.uuid4()),
        "target_type": "page",
        "project_id": project_id,
        "chapter_id": chapter.id if chapter else None,
        "chapter_number": chapter_number,
        "chapter_title": chapter.title if chapter else None,
        "page_number": page_number,
        "requested_by": user_id,
        "status": "queued",
        "created_at": now,
        "updated_at": now,
        "page_path": str(page_path) if page_path else None,
        "prompt_path": str(prompt_path) if prompt_path else None,
        "prompt_metadata": prompt_metadata,
        "prompt_context_hash": prompt_metadata.get("context_hash") if isinstance(prompt_metadata, dict) else None,
        "prompt_request_summary": prompt_metadata.get("request_summary") if isinstance(prompt_metadata, dict) else None,
        "character_image_references": character_image_references,
        "character_image_reference_count": len([ref for ref in character_image_references if ref.get("has_image")]),
        "worker_error": None,
    }


def _enqueue_regen_task(project_id: str, task: dict[str, Any]) -> None:
    regen_state = _load_regen_state(project_id)
    latest_by_target = regen_state.setdefault("latest_by_target", {})
    latest_by_target[_task_target_key(task["chapter_number"], task["page_number"])] = task
    _save_regen_state(project_id, regen_state)
    _append_jsonl(_project_root(project_id) / REGEN_QUEUE_FILE, task)


def _update_regen_task_status(project_id: str, chapter_number: int, page_number: int, status_value: str, error: str | None = None) -> None:
    regen_state = _load_regen_state(project_id)
    target_key = _task_target_key(chapter_number, page_number)
    task = regen_state.get("latest_by_target", {}).get(target_key)
    if task:
        task["status"] = status_value
        task["updated_at"] = _utc_now_iso()
        if error is not None:
            task["worker_error"] = error
        elif status_value in {"queued", "running", "completed"}:
            task["worker_error"] = None
        _save_regen_state(project_id, regen_state)


async def _load_image_settings(user_id: str, db: AsyncSession) -> dict[str, Any]:
    """从用户 Settings 表读取图片生成配置。"""
    result = await db.execute(select(UserSettings).where(UserSettings.user_id == user_id))
    user_settings = result.scalar_one_or_none()
    if not user_settings:
        raise RuntimeError("请先在设置页完成图片生成配置")
    api_key = user_settings.cover_api_key or ""
    base_url = resolve_image_api_base_url(
        provider=user_settings.cover_api_provider,
        base_url=user_settings.cover_api_base_url,
        model=user_settings.cover_image_model,
    )
    model = user_settings.cover_image_model or ""
    provider = user_settings.cover_api_provider or ""
    if not api_key or not model:
        raise RuntimeError("图片生成配置不完整，请在设置页填写封面图片的 API Key 和模型")
    profile = resolve_image_provider_profile(provider=provider, base_url=base_url, model=model)
    image_text_language = normalize_image_text_language(getattr(user_settings, "image_text_language", None))
    return {
        "api_key": api_key,
        "base_url": base_url,
        "model": model,
        "provider": provider,
        "provider_profile": profile.as_dict(),
        "image_text_language": image_text_language,
    }


async def _load_image_text_language(user_id: str, db: AsyncSession) -> str:
    result = await db.execute(select(UserSettings.image_text_language).where(UserSettings.user_id == user_id))
    return normalize_image_text_language(result.scalar_one_or_none())


async def _build_background_ai_service(user_id: str, db: AsyncSession) -> AIService:
    user_settings = await get_user_ai_settings(db, user_id, create_if_missing=False)

    mcp_result = await db.execute(select(MCPPlugin).where(MCPPlugin.user_id == user_id))
    mcp_plugins = mcp_result.scalars().all()
    enable_mcp = any(plugin.enabled for plugin in mcp_plugins) if mcp_plugins else False
    no_timeout_config = AIClientConfig(
        http=HTTPClientConfig(
            connect_timeout=None,
            read_timeout=None,
            write_timeout=None,
            pool_timeout=None,
        )
    )
    return AIService(
        api_provider=user_settings.api_provider,
        api_key=user_settings.api_key,
        api_base_url=user_settings.api_base_url or "",
        default_model=user_settings.llm_model,
        default_temperature=user_settings.temperature,
        default_max_tokens=user_settings.max_tokens,
        default_system_prompt=user_settings.system_prompt,
        config=no_timeout_config,
        user_id=user_id,
        db_session=db,
        enable_mcp=enable_mcp,
    )


async def _call_hermes_image_api(
    prompt: str,
    *,
    api_key: str,
    base_url: str,
    model: str,
    size: str = "720x1280",
    seed: int | None = None,
    reference_images: list[dict[str, Any]] | None = None,
    provider_profile: dict[str, Any] | None = None,
) -> bytes:
    configured_base_url = base_url.rstrip("/")
    if not configured_base_url or not api_key:
        raise RuntimeError("图片接口未配置，请在设置页配置封面图片的 API Key 和 API 地址")

    payload = build_image_generation_payload(
        prompt,
        model=model,
        size=size,
        seed=seed,
        reference_images=reference_images,
        provider_profile=provider_profile,
    )
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    base_candidates = normalize_image_api_base_urls(configured_base_url)
    logger.info("漫画页使用图片接口候选路径: %s", base_candidates)

    timeout = httpx.Timeout(
        COMIC_IMAGE_GENERATION_TIMEOUT_SECONDS,
        connect=COMIC_IMAGE_CONNECT_TIMEOUT_SECONDS,
        write=COMIC_IMAGE_WRITE_TIMEOUT_SECONDS,
        pool=COMIC_IMAGE_POOL_TIMEOUT_SECONDS,
    )
    async with httpx.AsyncClient(timeout=timeout) as client:
        last_exc: Exception | None = None
        for candidate_url in base_candidates or [configured_base_url]:
            try:
                response = await client.post(f"{candidate_url}/images/generations", json=payload, headers=headers)
                response.raise_for_status()
                break
            except httpx.HTTPStatusError as exc:
                last_exc = exc
                if exc.response.status_code == 404 and candidate_url != (base_candidates or [configured_base_url])[-1]:
                    logger.warning("漫画页图片接口 404，尝试下一个候选路径: %s", candidate_url)
                    continue
                raise RuntimeError(f"图片接口错误: {_image_error_detail(exc)}") from exc
            except httpx.TimeoutException as exc:
                raise RuntimeError("图片接口响应超时，请稍后重试") from exc
            except httpx.HTTPError as exc:
                raise RuntimeError(f"图片请求失败: {exc}") from exc
        else:
            raise RuntimeError("图片接口候选路径全部不可用")

    try:
        response_data = response.json()
    except json.JSONDecodeError as exc:
        raise RuntimeError("图片接口返回了无效 JSON") from exc

    try:
        image_bytes, _ = decode_b64_image_response(response_data)
        return image_bytes
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc


async def _generate_comic_image_with_rewrite_retry(
    prompt_text: str,
    *,
    api_key: str,
    base_url: str,
    model: str,
    size: str = "720x1280",
    seed: int | None = None,
    reference_images: list[dict[str, Any]] | None = None,
    provider_profile: dict[str, Any] | None = None,
    image_text_language: str | None = None,
) -> tuple[bytes, str, list[dict[str, Any]]]:
    errors: list[dict[str, Any]] = []
    attempts = _comic_prompt_attempts(prompt_text, image_text_language=image_text_language)
    for prompt_index, (rewrite_mode, candidate_prompt) in enumerate(attempts):
        max_capacity_attempts = (
            COMIC_IMAGE_FIRST_PROMPT_MAX_ATTEMPTS
            if prompt_index == 0
            else COMIC_IMAGE_REWRITE_PROMPT_MAX_ATTEMPTS
        )
        for capacity_attempt in range(1, max_capacity_attempts + 1):
            try:
                image_bytes = await _call_hermes_image_api(
                    candidate_prompt,
                    api_key=api_key,
                    base_url=base_url,
                    model=model,
                    size=size,
                    seed=seed,
                    reference_images=reference_images,
                    provider_profile=provider_profile,
                )
                return image_bytes, candidate_prompt, errors
            except Exception as exc:
                detail = _image_error_detail(exc)
                errors.append(
                    {
                        "rewrite_mode": rewrite_mode,
                        "attempt": capacity_attempt,
                        "error": detail,
                    }
                )
                logger.warning(
                    "漫画页生图失败，将按规则重试: mode=%s attempt=%s/%s error=%s",
                    rewrite_mode,
                    capacity_attempt,
                    max_capacity_attempts,
                    detail,
                )
                if capacity_attempt < max_capacity_attempts and _should_retry_comic_image_error(exc):
                    await asyncio.sleep(_comic_image_retry_delay(capacity_attempt, exc))
                    continue
                if not _should_retry_comic_image_error(exc) and prompt_index >= len(attempts) - 1:
                    raise RuntimeError(detail) from exc
                break
    raise RuntimeError("; ".join(item["error"] for item in errors[-3:]) or "漫画页生图失败")


async def _execute_page_regen(project_id: str, chapter_number: int, page_number: int, prompt_path: str | None, user_id: str) -> None:
    _update_regen_task_status(project_id, chapter_number, page_number, "running")
    try:
        from sqlalchemy.ext.asyncio import async_sessionmaker
        engine = await get_engine(user_id)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as img_db:
            img_settings = await _load_image_settings(user_id, img_db)
            project = await img_db.get(Project, project_id)
            artifact = await _get_page_artifact(project_id, chapter_number, page_number, img_db)

        original_prompt_text = ""
        if prompt_path and Path(prompt_path).is_file():
            original_prompt_text = Path(prompt_path).read_text(encoding="utf-8").strip()
        elif artifact and artifact.prompt_text:
            original_prompt_text = artifact.prompt_text.strip()
        if not original_prompt_text:
            raise RuntimeError(f"提示词文件不存在且数据库中没有可用提示词: {prompt_path}")

        regen_state = _load_regen_state(project_id)
        regen_task = _latest_task_for_page(regen_state, chapter_number, page_number) or {}
        provider_profile = img_settings.get("provider_profile") or {}
        page_generation = ComicWorkflowAgent.prepare_page_generation(
            project_id=project_id,
            chapter_number=chapter_number,
            page_number=page_number,
            original_prompt_text=original_prompt_text,
            regen_task=regen_task,
            provider_profile=provider_profile,
        )
        seed = page_generation["seed"]
        reference_images = page_generation["reference_images"]

        image_bytes, final_prompt_text, _retry_errors = await _generate_comic_image_with_rewrite_retry(
            _append_project_comic_style(original_prompt_text, project),
            api_key=img_settings["api_key"],
            base_url=img_settings["base_url"],
            model=img_settings["model"],
            size="720x1280",
            seed=seed,
            reference_images=reference_images,
            provider_profile=provider_profile,
            image_text_language=img_settings.get("image_text_language"),
        )
        prompt_text = final_prompt_text
        page_file_name = _new_comic_page_file(project_id, chapter_number, page_number).name
        image_cos_metadata = await _upload_comic_page_artifact(
            project_id=project_id,
            chapter_number=chapter_number,
            page_file_name=page_file_name,
            content=image_bytes,
            content_type="image/png",
        )
        if prompt_path:
            repaired_prompt_path = Path(prompt_path).with_name(f"{Path(prompt_path).stem}_last_used.txt")
            repaired_prompt_path.write_text(prompt_text + "\n", encoding="utf-8")
            prompt_metadata = _load_storyboard_prompt_metadata(Path(prompt_path)) or {}
            prompt_metadata.update(
                {
                    "last_used_at": _utc_now_iso(),
                    "last_used_prompt_length": len(prompt_text or ""),
                    "last_used_image_bytes": len(image_bytes),
                    "last_used_output_url": image_cos_metadata.url,
                    "provider_profile": provider_profile,
                    "seed": seed,
                    "reference_image_count": len(reference_images or []),
                    "page_character_names": page_generation["page_character_names"],
                }
            )
            _write_storyboard_prompt_metadata(Path(prompt_path), prompt_metadata)

        async with session_factory() as db:
            artifact = await _get_page_artifact(project_id, chapter_number, page_number, db)
            if artifact:
                artifact.status = "ready"
                artifact.prompt_text = prompt_text
                artifact.prompt_local_path = prompt_path
                artifact.image_local_path = None
                artifact.image_content_type = "image/png"
                artifact.image_content_length = len(image_bytes)
                artifact.failed_metadata = None
                artifact.error_message = None
            else:
                artifact = ComicPageArtifact(
                    project_id=project_id,
                    chapter_number=chapter_number,
                    page_number=page_number,
                    status="ready",
                    prompt_text=prompt_text,
                    prompt_local_path=prompt_path,
                    image_local_path=None,
                    image_content_type="image/png",
                    image_content_length=len(image_bytes),
                )
                db.add(artifact)
            _merge_cos_metadata("image", artifact, image_cos_metadata)
            await db.commit()

        _update_regen_task_status(project_id, chapter_number, page_number, "completed")
        logger.info("漫画页生成完成并上传 COS: project=%s chapter=%s page=%s url=%s", project_id, chapter_number, page_number, image_cos_metadata.url)

    except Exception as exc:
        error_msg = str(exc)
        logger.error("漫画页生成失败: project=%s chapter=%s page=%s error=%s", project_id, chapter_number, page_number, error_msg, exc_info=True)
        _update_regen_task_status(project_id, chapter_number, page_number, "failed", error_msg)
        try:
            from sqlalchemy.ext.asyncio import async_sessionmaker
            engine = await get_engine(user_id)
            session_factory = async_sessionmaker(engine, expire_on_commit=False)
            async with session_factory() as db:
                artifact = await _get_page_artifact(project_id, chapter_number, page_number, db)
                if artifact is None:
                    artifact = ComicPageArtifact(
                        project_id=project_id,
                        chapter_number=chapter_number,
                        page_number=page_number,
                    )
                    db.add(artifact)
                artifact.status = "failed"
                artifact.error_message = error_msg
                artifact.failed_metadata = json.dumps(
                    {
                        "category": "generation_failed",
                        "response_excerpt": error_msg[:1000],
                        "updated_at": _utc_now_iso(),
                    },
                    ensure_ascii=False,
                )
                await db.commit()
        except Exception:
            logger.warning("写入漫画页失败原因失败: project=%s chapter=%s page=%s", project_id, chapter_number, page_number, exc_info=True)


# ── 分镜改图 ──────────────────────────────────────────────


async def _read_current_page_image_bytes(project_id: str, chapter_number: int, page_number: int, db: AsyncSession) -> bytes:
    """从 COS 读取当前分镜图片 bytes"""
    page_artifact = await _get_page_artifact(project_id, chapter_number, page_number, db)

    if page_artifact and page_artifact.image_cos_object_key and tencent_cos_storage.is_enabled():
        try:
            content, _ = await tencent_cos_storage.download_bytes(object_key=page_artifact.image_cos_object_key)
            return content
        except Exception:
            logger.warning("改图读取 COS 图片失败: project=%s chapter=%s page=%s", project_id, chapter_number, page_number, exc_info=True)

    raise RuntimeError(f"无法读取第 {page_number} 页图片")


async def _call_image_edit_api_for_comic(
    edit_prompt: str,
    image_bytes: bytes,
    *,
    api_key: str,
    base_url: str,
    model: str,
    provider_profile: dict[str, Any] | None = None,
) -> bytes:
    """调用 /v1/images/edits 接口，基于原图进行修改"""
    configured_base_url = base_url.rstrip("/")
    if not configured_base_url or not api_key:
        raise RuntimeError("图片接口未配置，请在设置页配置封面图片的 API Key 和 API 地址")

    edit_model = resolve_image_edit_model(model, provider_profile=provider_profile)
    normalized_image_bytes = _normalize_comic_edit_input_image_bytes(image_bytes)

    headers = {"Authorization": f"Bearer {api_key}"}

    base_candidates = normalize_image_api_base_urls(configured_base_url)
    logger.info("分镜改图使用图片接口候选路径: %s", base_candidates)

    timeout = httpx.Timeout(600.0, connect=15.0)
    last_exc: Exception | None = None
    async with httpx.AsyncClient(timeout=timeout) as client:
        for candidate_url in base_candidates or [configured_base_url]:
            try:
                files = {"image": ("image.png", normalized_image_bytes, "image/png")}
                form_data = build_image_edit_payload(
                    edit_prompt,
                    model=edit_model,
                    size="720x1280",
                    provider_profile=provider_profile,
                )
                response = await client.post(
                    f"{candidate_url}/images/edits",
                    files=files,
                    data=form_data,
                    headers=headers,
                )
                response.raise_for_status()
                break
            except httpx.HTTPStatusError as exc:
                last_exc = exc
                if exc.response.status_code == 404 and candidate_url != base_candidates[-1]:
                    logger.warning("分镜改图接口 404，尝试下一个候选路径: %s", candidate_url)
                    continue
                detail = _image_error_detail(exc)
                raise RuntimeError(f"分镜改图接口错误: {detail}") from exc
            except httpx.TimeoutException as exc:
                raise RuntimeError("分镜改图接口响应超时，请稍后重试") from exc
            except httpx.HTTPError as exc:
                raise RuntimeError(f"分镜改图请求失败: {exc}") from exc
        else:
            raise RuntimeError("分镜改图接口候选路径全部不可用")

    try:
        response_data = response.json()
    except json.JSONDecodeError as exc:
        raise RuntimeError("分镜改图接口返回了无效 JSON") from exc

    try:
        image_bytes, _ = decode_b64_image_response(response_data)
        return image_bytes
    except ValueError as exc:
        raise RuntimeError(f"分镜改图接口错误: {exc}") from exc


async def _execute_page_edit(project_id: str, chapter_number: int, page_number: int, edit_prompt: str, user_id: str) -> None:
    """后台任务：基于原图改图"""
    _update_regen_task_status(project_id, chapter_number, page_number, "running")
    try:
        from sqlalchemy.ext.asyncio import async_sessionmaker
        engine = await get_engine(user_id)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)

        async with session_factory() as read_db:
            original_bytes = await _read_current_page_image_bytes(project_id, chapter_number, page_number, read_db)
            img_settings = await _load_image_settings(user_id, read_db)
            project = await read_db.get(Project, project_id)

        max_edit_attempts = 4
        last_edit_exc: Exception | None = None
        image_bytes: bytes | None = None
        for edit_attempt in range(1, max_edit_attempts + 1):
            try:
                image_bytes = await _call_image_edit_api_for_comic(
                    _append_project_comic_style(edit_prompt, project),
                    original_bytes,
                    api_key=img_settings["api_key"],
                    base_url=img_settings["base_url"],
                    model=img_settings["model"],
                    provider_profile=img_settings.get("provider_profile") or {},
                )
                break
            except Exception as retry_exc:
                last_edit_exc = retry_exc
                if edit_attempt < max_edit_attempts and _should_retry_comic_image_error(retry_exc):
                    wait = _comic_image_retry_delay(edit_attempt, retry_exc)
                    retry_message = f"上游图片服务繁忙，正在重试改图 {edit_attempt}/{max_edit_attempts - 1}，{wait} 秒后重试"
                    logger.warning(
                        "分镜改图遇到上游波动，准备重试: project=%s chapter=%s page=%s attempt=%s/%s wait=%ss error=%s",
                        project_id, chapter_number, page_number, edit_attempt, max_edit_attempts, wait, str(retry_exc)[:200],
                    )
                    _update_regen_task_status(project_id, chapter_number, page_number, "running", retry_message)
                    await asyncio.sleep(wait)
                    continue
                raise
        if image_bytes is None:
            raise last_edit_exc or RuntimeError("分镜改图失败")

        page_file_name = _new_comic_page_file(project_id, chapter_number, page_number).name
        image_cos_metadata = await _upload_comic_page_artifact(
            project_id=project_id,
            chapter_number=chapter_number,
            page_file_name=page_file_name,
            content=image_bytes,
            content_type="image/png",
        )

        async with session_factory() as db:
            artifact = await _get_page_artifact(project_id, chapter_number, page_number, db)
            if artifact:
                artifact.status = "ready"
                artifact.image_local_path = None
                artifact.image_content_type = "image/png"
                artifact.image_content_length = len(image_bytes)
                artifact.error_message = None
                artifact.failed_metadata = None
            else:
                artifact = ComicPageArtifact(
                    project_id=project_id,
                    chapter_number=chapter_number,
                    page_number=page_number,
                    status="ready",
                    image_local_path=None,
                    image_content_type="image/png",
                    image_content_length=len(image_bytes),
                )
                db.add(artifact)
            _merge_cos_metadata("image", artifact, image_cos_metadata)
            await db.commit()

        _update_regen_task_status(project_id, chapter_number, page_number, "completed")
        logger.info("分镜改图完成并上传 COS: project=%s chapter=%s page=%s url=%s", project_id, chapter_number, page_number, image_cos_metadata.url)

    except Exception as exc:
        error_msg = str(exc)
        logger.error("分镜改图失败: project=%s chapter=%s page=%s error=%s", project_id, chapter_number, page_number, error_msg, exc_info=True)
        _update_regen_task_status(project_id, chapter_number, page_number, "failed", error_msg)
        try:
            from sqlalchemy.ext.asyncio import async_sessionmaker
            engine = await get_engine(user_id)
            session_factory = async_sessionmaker(engine, expire_on_commit=False)
            async with session_factory() as db:
                artifact = await _get_page_artifact(project_id, chapter_number, page_number, db)
                if artifact:
                    artifact.status = "failed"
                    artifact.error_message = error_msg
                    artifact.failed_metadata = json.dumps(
                        {"category": "edit_failed", "response_excerpt": error_msg[:1000], "updated_at": _utc_now_iso()},
                        ensure_ascii=False,
                    )
                    await db.commit()
        except Exception:
            logger.warning("写入分镜改图失败原因失败: project=%s chapter=%s page=%s", project_id, chapter_number, page_number, exc_info=True)


async def _build_comic_chapter_regen_context(
    project_id: str,
    chapter_number: int,
    db: AsyncSession,
    *,
    allow_empty: bool = False,
    skip_existing_pages: bool = False,
) -> dict[str, Any] | None:
    project_result = await db.execute(select(Project).where(Project.id == project_id))
    project = project_result.scalar_one_or_none()
    chapter_map = await _get_project_chapter_map(project_id, db)
    chapter = chapter_map.get(chapter_number)
    if chapter is None:
        if allow_empty:
            return None
        raise HTTPException(status_code=404, detail="目标章节不存在")

    storyboard_artifact = await _get_storyboard_artifact(project_id, chapter_number, db)
    storyboard_state = _parse_int_keys(
        _read_json_file(_project_state_file(project_id, "storyboard")).get("scripted_chapters"),
        "scripted_chapters",
    ).get(chapter_number, {})
    storyboard_payload = await _load_storyboard_content_preferred(
        _resolve_storyboard_for_chapter(project_id, chapter_number),
        storyboard_state,
        storyboard_artifact,
    )
    storyboard_pages = _extract_storyboard_pages(storyboard_payload)
    storyboard_pages_by_number = _storyboard_page_map(storyboard_pages)
    page_artifact_map = await _get_page_artifact_map(project_id, db)
    manhua_scan = _resolve_manhua_for_chapter(project_id, chapter_number)
    failed_pages = _drop_stale_failed_pages(
        _extract_failed_pages(_read_json_file(_project_state_file(project_id, "manhua"))).get(chapter_number, {}),
        manhua_scan.get("pages", {}),
    )
    generated_entry = _parse_int_keys(
        _read_json_file(_project_state_file(project_id, "manhua")).get("generated_chapters"),
        "generated_chapters",
    ).get(chapter_number, {})
    page_numbers = _discover_chapter_page_numbers(
        generated_entry,
        manhua_scan,
        failed_pages,
        page_artifact_map,
        chapter_number,
        storyboard_pages,
    )
    skipped_existing_pages: list[int] = []
    if skip_existing_pages:
        original_page_numbers = list(page_numbers)
        page_numbers = _filter_missing_comic_page_numbers(
            page_numbers,
            manhua_scan,
            page_artifact_map,
            chapter_number,
        )
        skipped_existing_pages = [page_number for page_number in original_page_numbers if page_number not in page_numbers]
    if not page_numbers and not allow_empty:
        raise HTTPException(status_code=404, detail="该章节没有可重生成的漫画页面")

    continuity_context = await _build_storyboard_continuity_context(
        project_id=project_id,
        chapter_number=chapter_number,
        db=db,
        project=project,
        chapter=chapter,
    )

    return {
        "project": project,
        "chapter": chapter,
        "chapter_number": chapter_number,
        "page_numbers": page_numbers,
        "storyboard_pages_by_number": storyboard_pages_by_number,
        "manhua_scan": manhua_scan,
        "failed_pages": failed_pages,
        "page_artifact_map": page_artifact_map,
        "continuity_pack": continuity_context["continuity_pack"],
        "continuity_brief": continuity_context["continuity_brief"],
        "storyboard_characters_info": continuity_context["characters_info"],
        "character_image_references": await _build_character_image_references(project_id, chapter_number, db),
        "skipped_existing_pages": skipped_existing_pages,
    }


async def _queue_comic_chapter_page_regen_tasks(
    project_id: str,
    user_id: str,
    chapter_context: dict[str, Any],
    *,
    background_tasks: BackgroundTasks | None = None,
    run_inline: bool = False,
    max_concurrency: int | None = None,
) -> dict[str, Any]:
    chapter = chapter_context["chapter"]
    chapter_number = chapter_context["chapter_number"]
    page_numbers: list[int] = chapter_context["page_numbers"]
    storyboard_pages_by_number: dict[int, dict[str, Any]] = chapter_context["storyboard_pages_by_number"]
    manhua_scan: dict[str, Any] = chapter_context["manhua_scan"]
    failed_pages: dict[int, dict[str, Any]] = chapter_context["failed_pages"]
    page_artifact_map: dict[tuple[int, int], ComicPageArtifact] = chapter_context["page_artifact_map"]
    project = chapter_context["project"]
    continuity_pack = chapter_context.get("continuity_brief") or chapter_context.get("continuity_pack")
    character_image_references = chapter_context.get("character_image_references") or []
    regen_state = _load_regen_state(project_id)
    inline_jobs: list[tuple[int, str | None]] = []
    image_text_language = await _load_image_text_language(user_id, chapter_context["db"])

    queued_tasks: list[dict[str, Any]] = []
    skipped_pages: list[int] = []
    for page_number in page_numbers:
        existing_task = _latest_task_for_page(regen_state, chapter_number, page_number)
        if existing_task and existing_task.get("status") in {"queued", "running"}:
            skipped_pages.append(page_number)
            continue

        page_path = None
        prompt_path = manhua_scan.get("prompts", {}).get(page_number) or (
            Path(page_artifact_map[(chapter_number, page_number)].prompt_local_path)
            if (chapter_number, page_number) in page_artifact_map
            and page_artifact_map[(chapter_number, page_number)].prompt_local_path
            else None
        )
        if page_number in storyboard_pages_by_number:
            prompt_path = _ensure_storyboard_page_prompt_file(
                project_id=project_id,
                project_title=project.title if project else None,
                chapter_number=chapter_number,
                chapter_title=chapter.title if chapter else None,
                page_number=page_number,
                page_data=storyboard_pages_by_number[page_number],
                total_pages=len(storyboard_pages_by_number) or None,
                continuity_pack=continuity_pack,
                page_context=_storyboard_page_window_brief(storyboard_pages_by_number, page_number),
                character_reference_brief=_character_image_reference_brief(
                    character_image_references,
                    page_data=storyboard_pages_by_number[page_number],
                ),
                comic_style_instruction=_project_comic_style_instruction(project),
                image_text_language=image_text_language,
            )

        task = await _build_regen_task(
            project_id=project_id,
            user_id=user_id,
            chapter_number=chapter_number,
            page_number=page_number,
            chapter=chapter,
            page_path=page_path,
            prompt_path=prompt_path,
            db=chapter_context["db"],
        )
        if page_number in failed_pages:
            task["previous_failure"] = failed_pages[page_number]
        _enqueue_regen_task(project_id, task)
        queued_tasks.append(task)

        resolved_prompt_path = str(task.get("prompt_path")) if task.get("prompt_path") else None
        if run_inline:
            inline_jobs.append((page_number, resolved_prompt_path))
        elif background_tasks is not None:
            background_tasks.add_task(_execute_page_regen, project_id, chapter_number, page_number, resolved_prompt_path, user_id)

    if run_inline and inline_jobs:
        concurrency = min(_normalize_comic_page_concurrency(max_concurrency), len(inline_jobs))
        semaphore = asyncio.Semaphore(concurrency)

        async def _run_inline_page_regen(page_number: int, resolved_prompt_path: str | None) -> None:
            async with semaphore:
                try:
                    await _execute_page_regen(project_id, chapter_number, page_number, resolved_prompt_path, user_id)
                except Exception:
                    logger.exception(
                        "漫画页并发执行异常: project=%s chapter=%s page=%s",
                        project_id,
                        chapter_number,
                        page_number,
                    )

        await asyncio.gather(
            *[
                _run_inline_page_regen(page_number, resolved_prompt_path)
                for page_number, resolved_prompt_path in inline_jobs
            ]
        )

    return {
        "queued_count": len(queued_tasks),
        "skipped_pages": skipped_pages,
        "tasks": queued_tasks,
        "status": "queued" if queued_tasks else "running",
        "chapter_number": chapter_number,
        "concurrency": _normalize_comic_page_concurrency(max_concurrency),
    }


async def _execute_queued_comic_page_tasks(
    project_id: str,
    chapter_number: int,
    tasks: list[dict[str, Any]],
    user_id: str,
    *,
    max_concurrency: int | None = None,
) -> None:
    concurrency = min(_normalize_comic_page_concurrency(max_concurrency), max(len(tasks), 1))
    semaphore = asyncio.Semaphore(concurrency)

    async def _run_task(task: dict[str, Any]) -> None:
        page_number = task.get("page_number")
        if not isinstance(page_number, int):
            return
        prompt_path = str(task.get("prompt_path")) if task.get("prompt_path") else None
        async with semaphore:
            try:
                await _execute_page_regen(project_id, chapter_number, page_number, prompt_path, user_id)
            except Exception:
                logger.exception(
                    "漫画页后台并发执行异常: project=%s chapter=%s page=%s",
                    project_id,
                    chapter_number,
                    page_number,
                )

    if tasks:
        await asyncio.gather(*[_run_task(task) for task in tasks])


async def _execute_comic_batch_generation(
    task_id: str,
    project_id: str,
    chapter_numbers: list[int],
    user_id: str,
    comic_page_concurrency: int | None = None,
) -> None:
    task = _comic_batch_tasks.get(task_id) or _latest_comic_batch_task(project_id, task_id)
    if not task:
        return

    _comic_batch_tasks[task_id] = task

    def _persist() -> None:
        _comic_batch_tasks[task_id] = task
        _upsert_comic_batch_task(project_id, task)

    task["status"] = "running"
    task["updated_at"] = _utc_now_iso()
    task["errors"] = task.get("errors", [])
    task["skipped_chapters"] = task.get("skipped_chapters", [])
    task["chapter_results"] = task.get("chapter_results", [])
    task_options = task.setdefault("options", {})
    if isinstance(task_options, dict):
        comic_page_concurrency = _normalize_comic_page_concurrency(
            task_options.get("comic_page_concurrency", comic_page_concurrency)
        )
        task_options["comic_page_concurrency"] = comic_page_concurrency
    else:
        comic_page_concurrency = _normalize_comic_page_concurrency(comic_page_concurrency)
    _persist()

    try:
        from sqlalchemy.ext.asyncio import async_sessionmaker

        engine = await get_engine(user_id)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)

        total_chapters = len(chapter_numbers)
        for index, chapter_number in enumerate(chapter_numbers, start=1):
            task["current_chapter_number"] = chapter_number
            task["completed"] = index - 1
            task["updated_at"] = _utc_now_iso()
            _persist()

            try:
                async with session_factory() as db:
                    context = await _build_comic_chapter_regen_context(project_id, chapter_number, db, allow_empty=True)
                    if not context or not context["page_numbers"]:
                        task["skipped_chapters"].append(
                            {
                                "chapter_number": chapter_number,
                                "reason": "该章节没有可重生成的漫画页面",
                            }
                        )
                        task["completed"] = index
                        _persist()
                        continue

                    context["db"] = db
                    chapter_result = await _queue_comic_chapter_page_regen_tasks(
                        project_id,
                        user_id,
                        context,
                        run_inline=True,
                        max_concurrency=comic_page_concurrency,
                    )
                    chapter_result["chapter_number"] = chapter_number
                    task["chapter_results"].append(chapter_result)
                    _persist()
            except Exception as chapter_exc:
                error_text = str(chapter_exc)
                task["errors"].append({"chapter_number": chapter_number, "error": error_text})
                logger.warning(
                    "漫画批量生成章节失败: project=%s chapter=%s error=%s",
                    project_id,
                    chapter_number,
                    error_text,
                    exc_info=True,
                )
                _persist()

            task["completed"] = index
            task["updated_at"] = _utc_now_iso()
            _persist()

        task["total"] = total_chapters
        task["current_chapter_number"] = None
        task["status"] = "completed"
        task["updated_at"] = _utc_now_iso()
        _persist()
    except Exception as exc:
        logger.error("漫画批量生成失败: project=%s task=%s error=%s", project_id, task_id, exc, exc_info=True)
        task["status"] = "failed"
        task["error"] = str(exc)
        task["updated_at"] = _utc_now_iso()
        _persist()


def _resolve_storyboard_for_chapter(project_id: str, chapter_number: int) -> dict[str, Any]:
    return _scan_storyboards(project_id).get(chapter_number, {})


def _resolve_manhua_for_chapter(project_id: str, chapter_number: int) -> dict[str, Any]:
    return _scan_manhua(project_id).get(chapter_number, {})


async def _build_project_chapters_payload(project_id: str, db: AsyncSession) -> list[dict[str, Any]]:
    chapter_map = await _get_project_chapter_map(project_id, db)
    storyboard_artifacts = await _get_storyboard_artifact_map(project_id, db)
    page_artifacts = await _get_page_artifact_map(project_id, db)
    storyboard_state = _read_json_file(_project_state_file(project_id, "storyboard"))
    manhua_state = _read_json_file(_project_state_file(project_id, "manhua"))
    storyboard_entries = _parse_int_keys(storyboard_state.get("scripted_chapters"), "scripted_chapters")
    manhua_entries = _parse_int_keys(manhua_state.get("generated_chapters"), "generated_chapters")
    failed_pages = _extract_failed_pages(manhua_state)
    storyboard_scan = _scan_storyboards(project_id)
    manhua_scan = _scan_manhua(project_id)
    regen_state = _load_regen_state(project_id)

    chapter_numbers = set(chapter_map.keys())
    chapter_numbers.update(storyboard_entries.keys())
    chapter_numbers.update(manhua_entries.keys())
    chapter_numbers.update(storyboard_scan.keys())
    chapter_numbers.update(manhua_scan.keys())
    chapter_numbers.update(failed_pages.keys())
    chapter_numbers.update(storyboard_artifacts.keys())
    chapter_numbers.update(chapter_number for chapter_number, _ in page_artifacts.keys())

    chapters_payload: list[dict[str, Any]] = []
    for chapter_number in sorted(chapter_numbers):
        chapters_payload.append(
            _build_chapter_listing_payload(
                project_id=project_id,
                chapter_number=chapter_number,
                chapter_map=chapter_map,
                storyboard_entries=storyboard_entries,
                manhua_entries=manhua_entries,
                failed_pages=failed_pages,
                storyboard_scan=storyboard_scan,
                manhua_scan=manhua_scan,
                regen_state=regen_state,
                storyboard_artifacts=storyboard_artifacts,
                page_artifacts=page_artifacts,
            )
        )
    return chapters_payload


@router.get("/projects/{project_id}", summary="获取项目漫画章节状态")
@router.get("/projects/{project_id}/chapters", summary="获取项目漫画章节状态")
async def get_project_comic_chapters(
    project_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user_id = getattr(request.state, "user_id", None)
    await verify_project_access(project_id, user_id, db)
    chapters_payload = await _build_project_chapters_payload(project_id, db)

    return {
        "project_id": project_id,
        "chapters": chapters_payload,
        "summary": {
            "chapter_count": len(chapters_payload),
            "storyboard_count": sum(1 for chapter in chapters_payload if chapter["storyboard"]["exists"]),
            "image_page_count": sum(chapter["available_page_count"] for chapter in chapters_payload),
            "failed_page_count": sum(len(chapter["failed_page_numbers"]) for chapter in chapters_payload),
        },
    }


@router.get("/projects/{project_id}/read", summary="获取漫画连续阅读数据")
async def get_project_comic_reading(
    project_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user_id = getattr(request.state, "user_id", None)
    await verify_project_access(project_id, user_id, db)
    chapters_payload = await _build_project_chapters_payload(project_id, db)
    return _build_continuous_reading_payload(project_id, chapters_payload)


@router.get("/projects/{project_id}/chapters/{chapter_number}/storyboard", summary="获取章节分镜内容")
async def get_chapter_storyboard(
    project_id: str,
    request: Request,
    chapter_number: int = ApiPath(..., ge=1),
    db: AsyncSession = Depends(get_db),
):
    user_id = getattr(request.state, "user_id", None)
    await verify_project_access(project_id, user_id, db)

    storyboard_paths = _resolve_storyboard_for_chapter(project_id, chapter_number)
    storyboard_artifact = await _get_storyboard_artifact(project_id, chapter_number, db)
    storyboard_state = _parse_int_keys(
        _read_json_file(_project_state_file(project_id, "storyboard")).get("scripted_chapters"),
        "scripted_chapters",
    ).get(chapter_number, {})
    if not storyboard_paths.get("json_path") and not storyboard_paths.get("md_path") and not _storyboard_artifact_exists(storyboard_artifact):
        raise HTTPException(status_code=404, detail="分镜文件不存在")

    payload = await _load_storyboard_content_preferred(storyboard_paths, storyboard_state, storyboard_artifact)
    payload.update({"project_id": project_id, "chapter_number": chapter_number})
    return payload


@router.get(
    "/projects/{project_id}/chapters/{chapter_number}/combined",
    summary="获取章节正文、分镜和漫画聚合信息",
)
async def get_chapter_combined(
    project_id: str,
    request: Request,
    chapter_number: int = ApiPath(..., ge=1),
    db: AsyncSession = Depends(get_db),
):
    user_id = getattr(request.state, "user_id", None)
    await verify_project_access(project_id, user_id, db)

    chapter_map = await _get_project_chapter_map(project_id, db)
    storyboard_artifacts = await _get_storyboard_artifact_map(project_id, db)
    page_artifacts = await _get_page_artifact_map(project_id, db)
    storyboard_state = _read_json_file(_project_state_file(project_id, "storyboard"))
    manhua_state = _read_json_file(_project_state_file(project_id, "manhua"))
    storyboard_entries = _parse_int_keys(storyboard_state.get("scripted_chapters"), "scripted_chapters")
    manhua_entries = _parse_int_keys(manhua_state.get("generated_chapters"), "generated_chapters")
    failed_pages = _extract_failed_pages(manhua_state)
    storyboard_scan = _scan_storyboards(project_id)
    manhua_scan = _scan_manhua(project_id)
    regen_state = _load_regen_state(project_id)

    chapter_listing = _build_chapter_listing_payload(
        project_id=project_id,
        chapter_number=chapter_number,
        chapter_map=chapter_map,
        storyboard_entries=storyboard_entries,
        manhua_entries=manhua_entries,
        failed_pages=failed_pages,
        storyboard_scan=storyboard_scan,
        manhua_scan=manhua_scan,
        regen_state=regen_state,
        storyboard_artifacts=storyboard_artifacts,
        page_artifacts=page_artifacts,
    )

    if not chapter_listing["chapter_id"] and not chapter_listing["storyboard"]["exists"] and chapter_listing["page_count"] == 0:
        raise HTTPException(status_code=404, detail="章节不存在")

    chapter = chapter_map.get(chapter_number)
    character_image_references = await _build_character_image_references(project_id, chapter_number, db)
    storyboard_entry = storyboard_entries.get(chapter_number, {})
    storyboard = await _load_storyboard_content_preferred(
        storyboard_scan.get(chapter_number, {}),
        storyboard_entry,
        storyboard_artifacts.get(chapter_number),
    )

    return {
        "project_id": project_id,
        "chapter_number": chapter_number,
        "chapter": _chapter_detail_payload(chapter, chapter_number, chapter_listing["chapter_title"]),
        "storyboard": {
            "json_text": storyboard.get("json_text"),
            "json_content": storyboard.get("json_content"),
            "markdown_content": storyboard.get("markdown_content"),
            "character_image_references": character_image_references,
            "status": storyboard.get("status"),
            "updated_at": storyboard.get("updated_at"),
        },
        "comic": {
            "pages": chapter_listing["pages"],
            "page_count": chapter_listing["page_count"],
            "available_page_count": chapter_listing["available_page_count"],
            "chapter_status": chapter_listing["chapter_status"],
        },
    }


@router.get(
    "/projects/{project_id}/chapters/{chapter_number}/regeneration-status",
    summary="获取章节漫画重生成状态",
)
async def get_chapter_regeneration_status(
    project_id: str,
    request: Request,
    chapter_number: int = ApiPath(..., ge=1),
    db: AsyncSession = Depends(get_db),
):
    user_id = getattr(request.state, "user_id", None)
    await verify_project_access(project_id, user_id, db)

    chapter_map = await _get_project_chapter_map(project_id, db)
    storyboard_artifacts = await _get_storyboard_artifact_map(project_id, db)
    page_artifacts = await _get_page_artifact_map(project_id, db)
    storyboard_state = _read_json_file(_project_state_file(project_id, "storyboard"))
    manhua_state = _read_json_file(_project_state_file(project_id, "manhua"))
    storyboard_entries = _parse_int_keys(storyboard_state.get("scripted_chapters"), "scripted_chapters")
    manhua_entries = _parse_int_keys(manhua_state.get("generated_chapters"), "generated_chapters")
    failed_pages = _extract_failed_pages(manhua_state)
    storyboard_scan = _scan_storyboards(project_id)
    manhua_scan = _scan_manhua(project_id)
    regen_state = _load_regen_state(project_id)

    chapter_listing = _build_chapter_listing_payload(
        project_id=project_id,
        chapter_number=chapter_number,
        chapter_map=chapter_map,
        storyboard_entries=storyboard_entries,
        manhua_entries=manhua_entries,
        failed_pages=failed_pages,
        storyboard_scan=storyboard_scan,
        manhua_scan=manhua_scan,
        regen_state=regen_state,
        storyboard_artifacts=storyboard_artifacts,
        page_artifacts=page_artifacts,
    )

    if (
        not chapter_listing["chapter_id"]
        and not chapter_listing["storyboard"]["exists"]
        and chapter_listing["page_count"] == 0
    ):
        raise HTTPException(status_code=404, detail="章节不存在")

    return _build_chapter_regeneration_status_payload(project_id, chapter_listing)


@router.get(
    "/projects/{project_id}/regeneration-tasks/{task_id}",
    summary="获取单个漫画重生成任务状态",
)
async def get_regeneration_task_status(
    project_id: str,
    task_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user_id = getattr(request.state, "user_id", None)
    await verify_project_access(project_id, user_id, db)

    regen_state = _load_regen_state(project_id)
    task = _find_regen_task_by_id(regen_state, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return {
        "project_id": project_id,
        "task_id": task_id,
        "status": task.get("status"),
        "task": task,
    }


@router.put(
    "/projects/{project_id}/chapters/{chapter_number}/storyboard",
    summary="保存章节分镜内容",
)
async def update_chapter_storyboard(
    payload: StoryboardUpdateRequest,
    project_id: str,
    request: Request,
    chapter_number: int = ApiPath(..., ge=1),
    db: AsyncSession = Depends(get_db),
):
    user_id = getattr(request.state, "user_id", None)
    project = await verify_project_access(project_id, user_id, db)

    chapter_map = await _get_project_chapter_map(project_id, db)
    chapter = chapter_map.get(chapter_number)
    storyboard_artifact = await _get_storyboard_artifact(project_id, chapter_number, db)
    existing_storyboard_paths = _resolve_storyboard_for_chapter(project_id, chapter_number)
    existing_state = _read_json_file(_project_state_file(project_id, "storyboard"))
    scripted_chapters = existing_state.get("scripted_chapters")
    if not isinstance(scripted_chapters, dict):
        scripted_chapters = {}
        existing_state["scripted_chapters"] = scripted_chapters
    existing_entry = _parse_int_keys(scripted_chapters, "scripted_chapters").get(chapter_number, {})
    existing_storyboard = _load_storyboard_content(existing_storyboard_paths, existing_entry, storyboard_artifact)

    has_requested_update = any(
        value is not None
        for value in (payload.markdown_content, payload.json_text, payload.json_content)
    )
    if not has_requested_update:
        raise HTTPException(status_code=400, detail="至少需要提供 markdown_content、json_text 或 json_content 之一")

    markdown_content = (
        payload.markdown_content
        if payload.markdown_content is not None
        else existing_storyboard.get("markdown_content")
    )
    normalized_json_text = existing_storyboard.get("json_text")
    normalized_json_content = existing_storyboard.get("json_content")
    if payload.json_text is not None or payload.json_content is not None:
        try:
            normalized_json_text, normalized_json_content = _normalize_storyboard_json(
                payload.json_text,
                payload.json_content,
            )
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail="json_text 不是合法 JSON") from exc

    if markdown_content is None and normalized_json_text is None:
        raise HTTPException(status_code=400, detail="没有可写回的分镜内容")

    updated_at = _utc_now_iso()

    json_cos_metadata = None
    markdown_cos_metadata = None
    if normalized_json_text is not None:
        try:
            json_cos_metadata = await _upload_storyboard_artifact(
                project_id=project_id,
                chapter_number=chapter_number,
                suffix="json",
                content=(normalized_json_text + "\n").encode("utf-8"),
                content_type="application/json",
            )
        except Exception:
            logger.warning("上传分镜 JSON 到 COS 失败，继续保留数据库内容: project=%s chapter=%s", project_id, chapter_number, exc_info=True)
    if markdown_content is not None:
        try:
            markdown_cos_metadata = await _upload_storyboard_artifact(
                project_id=project_id,
                chapter_number=chapter_number,
                suffix="md",
                content=markdown_content.encode("utf-8"),
                content_type="text/markdown; charset=utf-8",
            )
        except Exception:
            logger.warning("上传分镜 Markdown 到 COS 失败，继续保留数据库内容: project=%s chapter=%s", project_id, chapter_number, exc_info=True)

    page_count, panel_count = _storyboard_page_panel_counts(normalized_json_content, markdown_content)
    chapter_key = str(chapter_number)
    state_entry = scripted_chapters.get(chapter_key)
    if not isinstance(state_entry, dict):
        state_entry = {}
        scripted_chapters[chapter_key] = state_entry
    state_entry.update(
        {
            "status": "edited" if existing_storyboard.get("exists") else "completed",
            "artifact_json": json_cos_metadata.url if json_cos_metadata else state_entry.get("artifact_json"),
            "artifact_md": markdown_cos_metadata.url if markdown_cos_metadata else state_entry.get("artifact_md"),
            "artifact": json_cos_metadata.url if json_cos_metadata else state_entry.get("artifact"),
            "markdown_artifact": markdown_cos_metadata.url if markdown_cos_metadata else state_entry.get("markdown_artifact"),
            "chapter_id": chapter.id if chapter else state_entry.get("chapter_id"),
            "chapter_title": (chapter.title if chapter else None) or state_entry.get("chapter_title"),
            "updated_at": updated_at,
        }
    )
    if page_count is not None:
        state_entry["page_count"] = page_count
    if panel_count is not None:
        state_entry["panel_count"] = panel_count

    existing_state["project_id"] = project_id
    project_title = getattr(project, "title", None)
    if project_title:
        existing_state["project_title"] = project_title
    existing_state.setdefault("failed_chapters", {})
    _write_json_file(_project_state_write_path(project_id, "storyboard"), existing_state)

    if storyboard_artifact is None:
        storyboard_artifact = ComicStoryboardArtifact(project_id=project_id, chapter_number=chapter_number)
        db.add(storyboard_artifact)
    storyboard_artifact.chapter_id = chapter.id if chapter else None
    storyboard_artifact.status = "edited" if existing_storyboard.get("exists") else "completed"
    storyboard_artifact.json_text = normalized_json_text
    storyboard_artifact.markdown_content = markdown_content
    storyboard_artifact.json_local_path = None
    storyboard_artifact.markdown_local_path = None
    storyboard_artifact.page_count = page_count
    storyboard_artifact.panel_count = panel_count
    storyboard_artifact.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    _merge_cos_metadata("json", storyboard_artifact, json_cos_metadata)
    _merge_cos_metadata("markdown", storyboard_artifact, markdown_cos_metadata)
    await db.commit()

    refreshed_storyboard_paths = _resolve_storyboard_for_chapter(project_id, chapter_number)
    refreshed_state_entry = _parse_int_keys(
        _read_json_file(_project_state_file(project_id, "storyboard")).get("scripted_chapters"),
        "scripted_chapters",
    ).get(chapter_number, {})
    refreshed_artifact = await _get_storyboard_artifact(project_id, chapter_number, db)
    response_payload = await _load_storyboard_content_preferred(refreshed_storyboard_paths, refreshed_state_entry, refreshed_artifact)
    response_payload.update(
        {
            "project_id": project_id,
            "chapter_number": chapter_number,
            "page_count": page_count,
            "panel_count": panel_count,
        }
    )
    return response_payload


@router.get("/projects/{project_id}/chapters/{chapter_number}/pages/{page_number}", summary="获取漫画页面图片")
async def get_chapter_page_image(
    project_id: str,
    request: Request,
    chapter_number: int = ApiPath(..., ge=1),
    page_number: int = ApiPath(..., ge=1),
    db: AsyncSession = Depends(get_db),
):
    user_id = getattr(request.state, "user_id", None)
    await verify_project_access(project_id, user_id, db)

    page_artifact = await _get_page_artifact(project_id, chapter_number, page_number, db)
    if page_artifact and page_artifact.image_cos_url:
        return RedirectResponse(url=page_artifact.image_cos_url, status_code=307)
    if page_artifact and page_artifact.image_cos_object_key and tencent_cos_storage.is_enabled():
        try:
            read_url = await tencent_cos_storage.get_read_url(object_key=page_artifact.image_cos_object_key)
            if read_url.startswith("http://") or read_url.startswith("https://"):
                return RedirectResponse(url=read_url, status_code=307)
        except Exception:
            logger.warning("获取漫画页 COS URL 失败，回退流式读取: project=%s chapter=%s page=%s", project_id, chapter_number, page_number, exc_info=True)
        try:
            content, content_type = await tencent_cos_storage.download_bytes(object_key=page_artifact.image_cos_object_key)
            return Response(content=content, media_type=content_type or page_artifact.image_content_type or "image/png")
        except Exception:
            logger.warning("读取漫画页 COS 对象失败: project=%s chapter=%s page=%s", project_id, chapter_number, page_number, exc_info=True)
    raise HTTPException(status_code=404, detail="漫画页面不存在")


@router.post(
    "/projects/{project_id}/chapters/{chapter_number}/pages/{page_number}/regenerate",
    status_code=status.HTTP_202_ACCEPTED,
    summary="重新生成漫画页面",
)
async def regenerate_chapter_page(
    project_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    chapter_number: int = ApiPath(..., ge=1),
    page_number: int = ApiPath(..., ge=1),
    db: AsyncSession = Depends(get_db),
):
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        raise HTTPException(status_code=401, detail="未登录")

    await verify_project_access(project_id, user_id, db)

    project_result = await db.execute(select(Project).where(Project.id == project_id))
    project = project_result.scalar_one_or_none()
    chapter_map = await _get_project_chapter_map(project_id, db)
    chapter = chapter_map.get(chapter_number)
    storyboard_artifact = await _get_storyboard_artifact(project_id, chapter_number, db)
    storyboard_state = _parse_int_keys(
        _read_json_file(_project_state_file(project_id, "storyboard")).get("scripted_chapters"),
        "scripted_chapters",
    ).get(chapter_number, {})
    storyboard_payload = await _load_storyboard_content_preferred(
        _resolve_storyboard_for_chapter(project_id, chapter_number),
        storyboard_state,
        storyboard_artifact,
    )
    storyboard_pages_by_number = _storyboard_page_map(_extract_storyboard_pages(storyboard_payload))
    continuity_context = await _build_storyboard_continuity_context(
        project_id=project_id,
        chapter_number=chapter_number,
        db=db,
        project=project,
        chapter=chapter,
    )
    character_image_references = await _build_character_image_references(project_id, chapter_number, db)
    image_text_language = await _load_image_text_language(user_id, db)
    page_artifact = await _get_page_artifact(project_id, chapter_number, page_number, db)
    manhua_scan = _resolve_manhua_for_chapter(project_id, chapter_number)
    failed_pages = _drop_stale_failed_pages(
        _extract_failed_pages(_read_json_file(_project_state_file(project_id, "manhua"))).get(chapter_number, {}),
        manhua_scan.get("pages", {}),
    )
    page_path = None
    prompt_path = manhua_scan.get("prompts", {}).get(page_number)
    if prompt_path is None and page_artifact and page_artifact.prompt_local_path:
        prompt_path = Path(page_artifact.prompt_local_path)
    if page_number in storyboard_pages_by_number:
        prompt_path = _ensure_storyboard_page_prompt_file(
            project_id=project_id,
            project_title=project.title if project else None,
            chapter_number=chapter_number,
            chapter_title=chapter.title if chapter else None,
            page_number=page_number,
            page_data=storyboard_pages_by_number[page_number],
            total_pages=len(storyboard_pages_by_number) or None,
            continuity_pack=continuity_context["continuity_brief"],
            page_context=_storyboard_page_window_brief(storyboard_pages_by_number, page_number),
            character_reference_brief=_character_image_reference_brief(
                character_image_references,
                page_data=storyboard_pages_by_number[page_number],
            ),
            comic_style_instruction=_project_comic_style_instruction(project),
            image_text_language=image_text_language,
        )
    failed_entry = failed_pages.get(page_number)
    if not chapter and not prompt_path and not failed_entry and page_artifact is None:
        raise HTTPException(status_code=404, detail="目标章节或页面不存在")

    regen_state = _load_regen_state(project_id)
    existing_task = _latest_task_for_page(regen_state, chapter_number, page_number)
    if existing_task and existing_task.get("status") in {"queued", "running"}:
        return {"status": existing_task["status"], "task": existing_task, "detail": "该页面已有进行中的重生成任务"}

    task = await _build_regen_task(project_id, user_id, chapter_number, page_number, chapter, page_path, prompt_path, db)
    if failed_entry:
        task["previous_failure"] = failed_entry
    _enqueue_regen_task(project_id, task)

    resolved_prompt_path = str(prompt_path) if prompt_path else task.get("prompt_path")
    background_tasks.add_task(_execute_page_regen, project_id, chapter_number, page_number, resolved_prompt_path, user_id)

    logger.info("漫画页重生成已入队并启动后台任务: project=%s chapter=%s page=%s task=%s", project_id, chapter_number, page_number, task["task_id"])
    return {"status": "queued", "task": task}


class ComicPageEditRequest(BaseModel):
    """分镜改图请求"""
    prompt: str = Field(..., min_length=1, max_length=2000, description="改图提示词（临时使用，不覆盖分镜内容）")


@router.post(
    "/projects/{project_id}/chapters/{chapter_number}/pages/{page_number}/edit",
    status_code=status.HTTP_202_ACCEPTED,
    summary="基于原图改图（单个分镜）",
)
async def edit_chapter_page(
    project_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    payload: ComicPageEditRequest,
    chapter_number: int = ApiPath(..., ge=1),
    page_number: int = ApiPath(..., ge=1),
    db: AsyncSession = Depends(get_db),
):
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        raise HTTPException(status_code=401, detail="未登录")

    await verify_project_access(project_id, user_id, db)

    # 校验分镜图片存在
    page_artifact = await _get_page_artifact(project_id, chapter_number, page_number, db)
    has_cos = bool(page_artifact and (page_artifact.image_cos_url or page_artifact.image_cos_object_key))
    if not has_cos:
        raise HTTPException(status_code=400, detail="当前分镜还没有图片，请先生成漫画")

    # 检查是否已有进行中的任务
    regen_state = _load_regen_state(project_id)
    existing_task = _latest_task_for_page(regen_state, chapter_number, page_number)
    if existing_task and existing_task.get("status") in {"queued", "running"}:
        return {"status": existing_task["status"], "task": existing_task, "detail": "该页面已有进行中的任务"}

    chapter_map = await _get_project_chapter_map(project_id, db)
    chapter = chapter_map.get(chapter_number)
    task = await _build_regen_task(project_id, user_id, chapter_number, page_number, chapter, None, None, db)
    task["target_type"] = "page_edit"
    task["edit_prompt"] = payload.prompt
    task["prompt_request_summary"] = payload.prompt[:120]
    _enqueue_regen_task(project_id, task)
    background_tasks.add_task(_execute_page_edit, project_id, chapter_number, page_number, payload.prompt, user_id)

    logger.info("分镜改图已入队: project=%s chapter=%s page=%s", project_id, chapter_number, page_number)
    return {"status": "queued", "task": task, "detail": "改图任务已加入后台队列"}


@router.post(
    "/projects/{project_id}/chapters/{chapter_number}/regenerate",
    status_code=status.HTTP_202_ACCEPTED,
    summary="整章重新生成漫画页面",
)
async def regenerate_chapter_pages(
    project_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    comic_page_concurrency: int | None = None,
    chapter_number: int = ApiPath(..., ge=1),
    db: AsyncSession = Depends(get_db),
):
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        raise HTTPException(status_code=401, detail="未登录")

    await verify_project_access(project_id, user_id, db)

    project_result = await db.execute(select(Project).where(Project.id == project_id))
    project = project_result.scalar_one_or_none()
    chapter_map = await _get_project_chapter_map(project_id, db)
    chapter = chapter_map.get(chapter_number)
    storyboard_artifact = await _get_storyboard_artifact(project_id, chapter_number, db)
    storyboard_state = _parse_int_keys(
        _read_json_file(_project_state_file(project_id, "storyboard")).get("scripted_chapters"),
        "scripted_chapters",
    ).get(chapter_number, {})
    storyboard_payload = await _load_storyboard_content_preferred(
        _resolve_storyboard_for_chapter(project_id, chapter_number),
        storyboard_state,
        storyboard_artifact,
    )
    storyboard_pages = _extract_storyboard_pages(storyboard_payload)
    storyboard_pages_by_number = _storyboard_page_map(storyboard_pages)
    continuity_context = await _build_storyboard_continuity_context(
        project_id=project_id,
        chapter_number=chapter_number,
        db=db,
        project=project,
        chapter=chapter,
    )
    character_image_references = await _build_character_image_references(project_id, chapter_number, db)
    image_text_language = await _load_image_text_language(user_id, db)
    page_artifacts = await _get_page_artifact_map(project_id, db)
    manhua_state = _read_json_file(_project_state_file(project_id, "manhua"))
    generated_entry = _parse_int_keys(manhua_state.get("generated_chapters"), "generated_chapters").get(chapter_number, {})
    manhua_scan = _resolve_manhua_for_chapter(project_id, chapter_number)
    failed_pages = _drop_stale_failed_pages(
        _extract_failed_pages(manhua_state).get(chapter_number, {}),
        manhua_scan.get("pages", {}),
    )
    page_numbers = _discover_chapter_page_numbers(
        generated_entry,
        manhua_scan,
        failed_pages,
        page_artifacts,
        chapter_number,
        storyboard_pages,
    )

    if not page_numbers:
        raise HTTPException(status_code=404, detail="该章节没有可重生成的漫画页面")

    regen_state = _load_regen_state(project_id)
    queued_tasks: list[dict[str, Any]] = []
    skipped_pages: list[int] = []
    for page_number in page_numbers:
        existing_task = _latest_task_for_page(regen_state, chapter_number, page_number)
        if existing_task and existing_task.get("status") in {"queued", "running"}:
            skipped_pages.append(page_number)
            continue

        page_path = None
        prompt_path = manhua_scan.get("prompts", {}).get(page_number) or (
            Path(page_artifacts[(chapter_number, page_number)].prompt_local_path)
            if (chapter_number, page_number) in page_artifacts
            and page_artifacts[(chapter_number, page_number)].prompt_local_path
            else None
        )
        if page_number in storyboard_pages_by_number:
            prompt_path = _ensure_storyboard_page_prompt_file(
                project_id=project_id,
                project_title=project.title if project else None,
                chapter_number=chapter_number,
                chapter_title=chapter.title if chapter else None,
                page_number=page_number,
                page_data=storyboard_pages_by_number[page_number],
                total_pages=len(storyboard_pages_by_number) or None,
                continuity_pack=continuity_context["continuity_brief"],
                page_context=_storyboard_page_window_brief(storyboard_pages_by_number, page_number),
                character_reference_brief=_character_image_reference_brief(
                    character_image_references,
                    page_data=storyboard_pages_by_number[page_number],
                ),
                comic_style_instruction=_project_comic_style_instruction(project),
                image_text_language=image_text_language,
            )

        task = await _build_regen_task(
            project_id=project_id,
            user_id=user_id,
            chapter_number=chapter_number,
            page_number=page_number,
            chapter=chapter,
            page_path=page_path,
            prompt_path=prompt_path,
            db=db,
        )
        if page_number in failed_pages:
            task["previous_failure"] = failed_pages[page_number]
        _enqueue_regen_task(project_id, task)
        queued_tasks.append(task)

    background_tasks.add_task(
        _execute_queued_comic_page_tasks,
        project_id,
        chapter_number,
        queued_tasks,
        user_id,
        max_concurrency=_normalize_comic_page_concurrency(comic_page_concurrency),
    )

    return {
        "status": "queued" if queued_tasks else "running",
        "chapter_number": chapter_number,
        "queued_count": len(queued_tasks),
        "skipped_pages": skipped_pages,
        "concurrency": _normalize_comic_page_concurrency(comic_page_concurrency),
        "tasks": queued_tasks,
    }


@router.post(
    "/projects/{project_id}/batch-generate",
    status_code=status.HTTP_202_ACCEPTED,
    summary="批量生成漫画章节页面",
)
async def batch_generate_chapter_comics(
    project_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    payload: ComicBatchGenerateRequest,
    db: AsyncSession = Depends(get_db),
):
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        raise HTTPException(status_code=401, detail="未登录")

    await verify_project_access(project_id, user_id, db)

    result = await db.execute(
        select(Chapter)
        .where(Chapter.project_id == project_id)
        .order_by(Chapter.chapter_number)
    )
    all_chapters = result.scalars().all()
    end_number = payload.start_chapter_number + payload.count - 1
    chapters_to_generate = [
        chapter for chapter in all_chapters
        if payload.start_chapter_number <= chapter.chapter_number <= end_number
    ]
    if not chapters_to_generate:
        raise HTTPException(status_code=400, detail="指定范围内没有可处理的章节")

    chapter_numbers = [chapter.chapter_number for chapter in chapters_to_generate]
    task_id = str(uuid.uuid4())
    _comic_batch_tasks[task_id] = {
        "task_id": task_id,
        "project_id": project_id,
        "type": "batch",
        "chapter_numbers": chapter_numbers,
        "status": "pending",
        "total": len(chapter_numbers),
        "completed": 0,
        "current_chapter_number": None,
        "errors": [],
        "skipped_chapters": [],
        "chapter_results": [],
        "created_at": _utc_now_iso(),
        "updated_at": _utc_now_iso(),
        "options": {
            "start_chapter_number": payload.start_chapter_number,
            "count": payload.count,
            "comic_page_concurrency": _normalize_comic_page_concurrency(payload.comic_page_concurrency),
        },
    }
    _upsert_comic_batch_task(project_id, _comic_batch_tasks[task_id])
    background_tasks.add_task(
        _execute_comic_batch_generation,
        task_id,
        project_id,
        chapter_numbers,
        user_id,
        _normalize_comic_page_concurrency(payload.comic_page_concurrency),
    )
    return {
        "task_id": task_id,
        "status": "pending",
        "total": len(chapter_numbers),
        "chapter_numbers": chapter_numbers,
        "message": f"批量漫画生成任务已创建，共 {len(chapter_numbers)} 章",
    }


@router.get("/projects/{project_id}/batch-generate/{task_id}/status", summary="查询漫画批量生成任务状态")
async def get_comic_batch_generate_status(
    project_id: str,
    task_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user_id = getattr(request.state, "user_id", None)
    await verify_project_access(project_id, user_id, db)

    task = _comic_batch_tasks.get(task_id) or _latest_comic_batch_task(project_id, task_id)
    if not task or task.get("project_id") != project_id:
        raise HTTPException(status_code=404, detail="任务不存在")
    _comic_batch_tasks[task_id] = task
    return task


@router.post(
    "/projects/{project_id}/full-batch-generate",
    status_code=status.HTTP_202_ACCEPTED,
    summary="批量生成章节、分镜和漫画",
)
async def batch_generate_full_pipeline(
    project_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    payload: ComicFullPipelineBatchGenerateRequest,
    db: AsyncSession = Depends(get_db),
):
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        raise HTTPException(status_code=401, detail="未登录")

    await verify_project_access(project_id, user_id, db)

    result = await db.execute(
        select(Chapter)
        .where(Chapter.project_id == project_id)
        .order_by(Chapter.chapter_number)
    )
    all_chapters = result.scalars().all()
    end_number = payload.start_chapter_number + payload.count - 1
    chapters_to_generate = [
        chapter for chapter in all_chapters
        if payload.start_chapter_number <= chapter.chapter_number <= end_number
    ]
    if not chapters_to_generate:
        raise HTTPException(status_code=400, detail="指定范围内没有可处理的章节")

    chapter_numbers = [chapter.chapter_number for chapter in chapters_to_generate]
    task_id = str(uuid.uuid4())
    task = {
        "task_id": task_id,
        "project_id": project_id,
        "type": "full_pipeline",
        "generation_mode": payload.generation_mode,
        "chapter_numbers": chapter_numbers,
        "status": "pending",
        "current_stage": "chapter",
        "total": len(chapter_numbers),
        "completed": 0,
        "successful": 0,
        "failed": 0,
        "current_chapter_number": None,
        "current_retry_count": None,
        "stages": _pipeline_stage_states(len(chapter_numbers), include_analysis=payload.enable_analysis),
        "errors": [],
        "chapter_results": [],
        "created_at": _utc_now_iso(),
        "started_at": None,
        "completed_at": None,
        "error_message": None,
        "options": {
            "style_id": payload.style_id,
            "target_word_count": payload.target_word_count,
            "enable_analysis": payload.enable_analysis,
            "enable_mcp": payload.enable_mcp,
            "max_retries": payload.max_retries,
            "model": payload.model,
            "target_pages": payload.target_pages,
            "comic_page_concurrency": _normalize_comic_page_concurrency(payload.comic_page_concurrency),
            "start_chapter_number": payload.start_chapter_number,
            "count": payload.count,
            "generation_mode": payload.generation_mode,
        },
    }
    _pipeline_batch_tasks[task_id] = task
    _upsert_pipeline_batch_task(project_id, task)
    background_tasks.add_task(
        _execute_full_pipeline_batch_generation,
        task_id,
        project_id,
        chapter_numbers,
        user_id,
        payload.model,
        payload.style_id,
        payload.target_word_count,
        payload.enable_analysis,
        payload.max_retries,
        payload.target_pages,
        payload.enable_mcp,
        payload.generation_mode,
        _normalize_comic_page_concurrency(payload.comic_page_concurrency),
    )
    return ComicFullPipelineBatchGenerateResponse(
        task_id=task_id,
        status="pending",
        generation_mode=payload.generation_mode,
        total=len(chapter_numbers),
        chapter_numbers=chapter_numbers,
        message=f"全流程批量{'增量补充' if payload.generation_mode == 'incremental' else '完整重建'}任务已创建，共 {len(chapter_numbers)} 章",
    )


@router.get("/projects/{project_id}/full-batch-generate/{task_id}/status", summary="查询全流程批量生成任务状态")
async def get_full_pipeline_batch_generate_status(
    project_id: str,
    task_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user_id = getattr(request.state, "user_id", None)
    await verify_project_access(project_id, user_id, db)

    task = _pipeline_batch_tasks.get(task_id) or _latest_pipeline_batch_task(project_id, task_id)
    if not task or task.get("project_id") != project_id:
        raise HTTPException(status_code=404, detail="任务不存在")
    _pipeline_batch_tasks[task_id] = task

    stages = task.get("stages") if isinstance(task.get("stages"), dict) else {}
    normalized_stages = {
        key: ComicPipelineStageStatusResponse(**value) if isinstance(value, dict) else ComicPipelineStageStatusResponse()
        for key, value in stages.items()
    }
    return ComicFullPipelineBatchStatusResponse(
        task_id=task["task_id"],
        project_id=project_id,
        status=task.get("status", "pending"),
        generation_mode=task.get("generation_mode") or (task.get("options") or {}).get("generation_mode"),
        current_stage=task.get("current_stage"),
        total=int(task.get("total") or len(task.get("chapter_numbers") or [])),
        completed=int(task.get("completed") or 0),
        successful=int(task.get("successful") or 0),
        failed=int(task.get("failed") or 0),
        chapter_numbers=list(task.get("chapter_numbers") or []),
        current_chapter_number=task.get("current_chapter_number"),
        current_retry_count=task.get("current_retry_count"),
        stages=normalized_stages,
        errors=list(task.get("errors") or []),
        chapter_results=list(task.get("chapter_results") or []),
        created_at=task.get("created_at"),
        started_at=task.get("started_at"),
        completed_at=task.get("completed_at"),
        error_message=task.get("error_message"),
    )


@router.get("/projects/{project_id}/full-batch-generate/active", summary="获取项目当前运行中的全流程批量任务")
async def get_active_full_pipeline_batch_generation(
    project_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user_id = getattr(request.state, "user_id", None)
    await verify_project_access(project_id, user_id, db)

    active_task = _latest_active_pipeline_batch_task(project_id)

    if not active_task:
        return {"has_active_task": False, "task": None}

    return {
        "has_active_task": True,
        "task": active_task,
    }


async def _execute_full_pipeline_batch_generation(
    task_id: str,
    project_id: str,
    chapter_numbers: list[int],
    user_id: str,
    custom_model: str | None,
    style_id: int | None,
    target_word_count: int,
    enable_analysis: bool,
    max_retries: int,
    target_pages: int,
    enable_mcp: bool,
    generation_mode: ComicFullPipelineGenerationMode = "incremental",
    comic_page_concurrency: int | None = None,
) -> None:
    task = _latest_pipeline_batch_task(project_id, task_id)
    if not task:
        return

    _upsert_pipeline_batch_task(project_id, task)

    def _persist() -> None:
        _pipeline_batch_tasks[task_id] = task
        _upsert_pipeline_batch_task(project_id, task)

    task["status"] = "running"
    task["current_stage"] = "chapter"
    task["started_at"] = task.get("started_at") or _utc_now_iso()
    task["updated_at"] = _utc_now_iso()
    task["stages"] = task.get("stages") or _pipeline_stage_states(
        len(chapter_numbers),
        include_analysis=enable_analysis,
    )
    _ensure_pipeline_stage_states(task, len(chapter_numbers), include_analysis=enable_analysis)
    task_generation_mode = task.get("generation_mode") or generation_mode
    if task_generation_mode not in {"full", "incremental"}:
        generation_mode = "incremental"
    else:
        generation_mode = task_generation_mode
    task["generation_mode"] = generation_mode
    task_options = task.setdefault("options", {})
    if isinstance(task_options, dict):
        task_options.setdefault("generation_mode", generation_mode)
        comic_page_concurrency = _normalize_comic_page_concurrency(
            task_options.get("comic_page_concurrency", comic_page_concurrency)
        )
        task_options["comic_page_concurrency"] = comic_page_concurrency
    else:
        comic_page_concurrency = _normalize_comic_page_concurrency(comic_page_concurrency)
    incremental_mode = generation_mode == "incremental"
    task["errors"] = task.get("errors") or []
    task["chapter_results"] = task.get("chapter_results") or []
    _persist()

    from sqlalchemy.ext.asyncio import async_sessionmaker

    engine = await get_engine(user_id)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    from app.api.chapters import (
        check_prerequisites,
        generate_single_chapter_for_batch,
        get_db_write_lock,
    )

    try:
        async with session_factory() as db:
            ai_service = await _build_background_ai_service(user_id, db)
            if not enable_mcp:
                ai_service.enable_mcp = False
            write_lock = await get_db_write_lock(user_id)
            last_generated_summary: str | None = None
            comic_page_semaphore = asyncio.Semaphore(_normalize_comic_page_concurrency(comic_page_concurrency))
            comic_page_jobs: list[asyncio.Task[None]] = []

            async def _run_pipeline_comic_page(
                *,
                chapter_number: int,
                page_number: int,
                prompt_path: str | None,
            ) -> None:
                async with comic_page_semaphore:
                    try:
                        await _execute_page_regen(project_id, chapter_number, page_number, prompt_path, user_id)
                    except Exception:
                        logger.exception(
                            "全流程漫画页异步生成异常: project=%s task=%s chapter=%s page=%s",
                            project_id,
                            task_id,
                            chapter_number,
                            page_number,
                        )

            total_chapters = len(chapter_numbers)
            for index, chapter_number in enumerate(chapter_numbers, start=1):
                if task.get("status") == "cancelled":
                    task["completed_at"] = _utc_now_iso()
                    _persist()
                    return

                chapter_result_entry: dict[str, Any] = {
                    "chapter_number": chapter_number,
                    "chapter_title": None,
                    "stage_results": {},
                    "errors": [],
                    "success": False,
                }
                chapter_failure_counted = False

                def _mark_chapter_failed_once() -> None:
                    nonlocal chapter_failure_counted
                    if chapter_failure_counted:
                        return
                    task["failed"] = int(task.get("failed") or 0) + 1
                    chapter_failure_counted = True

                task["current_chapter_number"] = chapter_number
                task["current_retry_count"] = 0
                task["updated_at"] = _utc_now_iso()
                _persist()

                chapter = None
                chapter_title = None
                chapter_generated = False
                chapter_error: str | None = None

                task["current_stage"] = "chapter"
                task["stages"]["chapter"]["current_chapter_number"] = chapter_number
                task["stages"]["chapter"]["current_retry_count"] = 0
                _persist()

                chapter_result = await db.execute(
                    select(Chapter).where(
                        Chapter.project_id == project_id,
                        Chapter.chapter_number == chapter_number,
                    )
                )
                chapter = chapter_result.scalar_one_or_none()
                if chapter:
                    chapter_title = chapter.title
                    chapter_result_entry["chapter_title"] = chapter_title

                chapter_should_generate = not (incremental_mode and _chapter_has_content(chapter))
                if not chapter:
                    chapter_error = f"章节 {chapter_number} 不存在"
                elif not chapter_should_generate:
                    existing_summary = _chapter_pipeline_context_summary(chapter)
                    if existing_summary:
                        last_generated_summary = existing_summary
                    chapter_result_entry["stage_results"]["chapter"] = {
                        "status": "skipped",
                        "reason": "已有章节正文",
                    }
                    task["stages"]["chapter"]["succeeded"] = int(task["stages"]["chapter"].get("succeeded") or 0) + 1
                    task["stages"]["chapter"]["error_message"] = None
                else:
                    chapter_retry = 0
                    while chapter_retry <= max_retries:
                        try:
                            chapter_result = await db.execute(
                                select(Chapter).where(
                                    Chapter.project_id == project_id,
                                    Chapter.chapter_number == chapter_number,
                                )
                            )
                            chapter = chapter_result.scalar_one_or_none()
                            if not chapter:
                                raise RuntimeError(f"章节 {chapter_number} 不存在")
                            chapter_title = chapter.title
                            chapter_result_entry["chapter_title"] = chapter_title

                            can_generate, error_msg, _ = await check_prerequisites(db, chapter)
                            if not can_generate:
                                raise RuntimeError(f"前置条件不满足: {error_msg}")

                            task["current_retry_count"] = chapter_retry
                            task["stages"]["chapter"]["current_retry_count"] = chapter_retry
                            _persist()

                            generated_summary = await generate_single_chapter_for_batch(
                                db_session=db,
                                chapter=chapter,
                                user_id=user_id,
                                style_id=style_id,
                                target_word_count=target_word_count,
                                ai_service=ai_service,
                                write_lock=write_lock,
                                custom_model=custom_model,
                                previous_summary_context=last_generated_summary,
                            )
                            if generated_summary:
                                last_generated_summary = f"第{chapter.chapter_number}章《{chapter.title}》：{generated_summary}"
                            chapter_generated = True
                            task["stages"]["chapter"]["succeeded"] = int(task["stages"]["chapter"].get("succeeded") or 0) + 1
                            task["stages"]["chapter"]["error_message"] = None
                            break
                        except Exception as exc:
                            chapter_error = str(exc)
                            chapter_retry += 1
                            task["stages"]["chapter"]["current_retry_count"] = chapter_retry - 1
                            task["stages"]["chapter"]["error_message"] = chapter_error
                            _persist()
                            if chapter_retry <= max_retries:
                                await asyncio.sleep(min(2 ** chapter_retry, 10))
                                continue
                            task["stages"]["chapter"]["failed"] = int(task["stages"]["chapter"].get("failed") or 0) + 1
                            task["errors"].append({
                                "chapter_number": chapter_number,
                                "stage": "chapter",
                                "error": chapter_error,
                            })
                            chapter_result_entry["errors"].append({
                                "stage": "chapter",
                                "error": chapter_error,
                            })
                            break

                chapter_stage_ok = chapter_generated or not chapter_should_generate

                if enable_analysis and chapter_stage_ok and chapter is not None:
                    task["current_stage"] = "analysis"
                    task["current_retry_count"] = 0
                    task["stages"]["analysis"]["current_chapter_number"] = chapter_number
                    task["stages"]["analysis"]["current_retry_count"] = 0
                    _persist()
                    analysis_state = await _get_chapter_analysis_state(chapter.id, db)
                    analysis_action = _shared_resolve_analysis_stage_action(generation_mode, analysis_state)

                    if analysis_action.get("action") == "skip":
                        stage_result = {
                            "status": "skipped",
                            "reason": analysis_action.get("reason") or "无需重复分析",
                        }
                        if analysis_action.get("analysis_id"):
                            stage_result["analysis_id"] = analysis_action["analysis_id"]
                        if analysis_action.get("task_id"):
                            stage_result["task_id"] = analysis_action["task_id"]
                            stage_result["task_status"] = analysis_action.get("task_status")
                        chapter_result_entry["stage_results"]["analysis"] = stage_result
                        task["stages"]["analysis"]["succeeded"] = int(task["stages"]["analysis"].get("succeeded") or 0) + 1
                        task["stages"]["analysis"]["processed"] = int(task["stages"]["analysis"].get("processed") or 0) + 1
                        task["stages"]["analysis"]["error_message"] = None
                    else:
                        analysis_retry = 0
                        analysis_error: str | None = None
                        from app.models.analysis_task import AnalysisTask
                        from app.api.chapters import analyze_chapter_background

                        while analysis_retry <= max_retries:
                            try:
                                task["current_retry_count"] = analysis_retry
                                task["stages"]["analysis"]["current_retry_count"] = analysis_retry
                                _persist()

                                async with write_lock:
                                    analysis_task = AnalysisTask(
                                        chapter_id=chapter.id,
                                        user_id=user_id,
                                        project_id=project_id,
                                        status="pending",
                                        progress=0,
                                    )
                                    db.add(analysis_task)
                                    await db.commit()
                                    await db.refresh(analysis_task)

                                analysis_result = await analyze_chapter_background(
                                    chapter_id=chapter.id,
                                    user_id=user_id,
                                    project_id=project_id,
                                    task_id=analysis_task.id,
                                    ai_service=ai_service,
                                )
                                if not analysis_result:
                                    raise RuntimeError("分析函数返回失败")
                                chapter_result_entry["stage_results"]["analysis"] = {
                                    "status": "completed",
                                    "task_id": analysis_task.id,
                                }
                                task["stages"]["analysis"]["succeeded"] = int(task["stages"]["analysis"].get("succeeded") or 0) + 1
                                task["stages"]["analysis"]["processed"] = int(task["stages"]["analysis"].get("processed") or 0) + 1
                                task["stages"]["analysis"]["error_message"] = None
                                break
                            except Exception as exc:
                                analysis_error = str(exc)
                                analysis_retry += 1
                                task["stages"]["analysis"]["current_retry_count"] = analysis_retry - 1
                                task["stages"]["analysis"]["error_message"] = analysis_error
                                _persist()
                                if analysis_retry <= max_retries:
                                    await asyncio.sleep(min(2 ** analysis_retry, 10))
                                    continue
                                task["stages"]["analysis"]["failed"] = int(task["stages"]["analysis"].get("failed") or 0) + 1
                                task["stages"]["analysis"]["processed"] = int(task["stages"]["analysis"].get("processed") or 0) + 1
                                task["errors"].append({
                                    "chapter_number": chapter_number,
                                    "stage": "analysis",
                                    "error": analysis_error,
                                })
                                _mark_chapter_failed_once()
                                chapter_result_entry["errors"].append({
                                    "stage": "analysis",
                                    "error": analysis_error,
                                })
                                chapter_result_entry["stage_results"]["analysis"] = {
                                    "status": "failed",
                                    "error": analysis_error,
                                }
                                break
                elif not enable_analysis:
                    chapter_result_entry["stage_results"]["analysis"] = {
                        "status": "skipped",
                        "reason": "同步分析未开启",
                    }

                if not chapter_stage_ok:
                    chapter_result_entry["stage_results"]["chapter"] = {
                        "status": "failed",
                        "error": chapter_error,
                    }
                    if chapter_error and not any(
                        err.get("stage") == "chapter" and err.get("error") == chapter_error
                        for err in chapter_result_entry["errors"]
                    ):
                        task["stages"]["chapter"]["failed"] = int(task["stages"]["chapter"].get("failed") or 0) + 1
                        task["stages"]["chapter"]["error_message"] = chapter_error
                        _mark_chapter_failed_once()
                        chapter_result_entry["errors"].append({
                            "stage": "chapter",
                            "error": chapter_error,
                        })
                        task["errors"].append({
                            "chapter_number": chapter_number,
                            "stage": "chapter",
                            "error": chapter_error,
                        })
                    elif chapter_error:
                        task["stages"]["chapter"]["error_message"] = chapter_error
                        _mark_chapter_failed_once()
                    task["completed"] = index
                    task["current_stage"] = "chapter"
                    task["stages"]["chapter"]["processed"] = int(task["stages"]["chapter"].get("processed") or 0) + 1
                    task["chapter_results"].append(chapter_result_entry)
                    _persist()
                    continue

                task["stages"]["chapter"]["processed"] = int(task["stages"]["chapter"].get("processed") or 0) + 1
                if "chapter" not in chapter_result_entry["stage_results"]:
                    chapter_result_entry["stage_results"]["chapter"] = {"status": "completed"}

                task["current_stage"] = "storyboard"
                task["current_retry_count"] = 0
                task["stages"]["storyboard"]["current_chapter_number"] = chapter_number
                task["stages"]["storyboard"]["current_retry_count"] = 0
                _persist()
                storyboard_success = False
                storyboard_error: str | None = None
                storyboard_should_generate = True
                existing_storyboard_page_count = 0
                if incremental_mode and not chapter_generated:
                    storyboard_artifact = await _get_storyboard_artifact(project_id, chapter_number, db)
                    storyboard_state = _parse_int_keys(
                        _read_json_file(_project_state_file(project_id, "storyboard")).get("scripted_chapters"),
                        "scripted_chapters",
                    ).get(chapter_number, {})
                    storyboard_payload = await _load_storyboard_content_preferred(
                        _resolve_storyboard_for_chapter(project_id, chapter_number),
                        storyboard_state,
                        storyboard_artifact,
                    )
                    existing_storyboard_page_count = len(_extract_storyboard_pages(storyboard_payload))
                    storyboard_should_generate = existing_storyboard_page_count == 0

                if not storyboard_should_generate:
                    storyboard_success = True
                    chapter_result_entry["stage_results"]["storyboard"] = {
                        "status": "skipped",
                        "reason": "已有分镜",
                        "page_count": existing_storyboard_page_count,
                    }
                    task["stages"]["storyboard"]["succeeded"] = int(task["stages"]["storyboard"].get("succeeded") or 0) + 1
                    task["stages"]["storyboard"]["processed"] = int(task["stages"]["storyboard"].get("processed") or 0) + 1
                    task["stages"]["storyboard"]["error_message"] = None
                else:
                    try:
                        storyboard_result = await _generate_storyboard_for_chapter(
                            project_id=project_id,
                            chapter_number=chapter_number,
                            user_id=user_id,
                            target_pages=target_pages,
                            db=db,
                            ai_service=ai_service,
                        )
                        storyboard_success = True
                        chapter_result_entry["stage_results"]["storyboard"] = {
                            "status": "completed",
                            "page_count": storyboard_result.get("page_count"),
                            "panel_count": storyboard_result.get("panel_count"),
                        }
                        task["stages"]["storyboard"]["succeeded"] = int(task["stages"]["storyboard"].get("succeeded") or 0) + 1
                        task["stages"]["storyboard"]["processed"] = int(task["stages"]["storyboard"].get("processed") or 0) + 1
                        task["stages"]["storyboard"]["error_message"] = None
                    except Exception as exc:
                        storyboard_error = str(exc)
                        task["stages"]["storyboard"]["failed"] = int(task["stages"]["storyboard"].get("failed") or 0) + 1
                        task["stages"]["storyboard"]["processed"] = int(task["stages"]["storyboard"].get("processed") or 0) + 1
                        task["stages"]["storyboard"]["error_message"] = storyboard_error
                        task["errors"].append({
                            "chapter_number": chapter_number,
                            "stage": "storyboard",
                            "error": storyboard_error,
                        })
                        chapter_result_entry["errors"].append({
                            "stage": "storyboard",
                            "error": storyboard_error,
                        })
                        chapter_result_entry["stage_results"]["storyboard"] = {
                            "status": "failed",
                            "error": storyboard_error,
                        }

                if not storyboard_success:
                    _mark_chapter_failed_once()
                    task["completed"] = index
                    task["chapter_results"].append(chapter_result_entry)
                    _persist()
                    continue

                task["current_stage"] = "comic"
                task["current_retry_count"] = 0
                task["stages"]["comic"]["current_chapter_number"] = chapter_number
                task["stages"]["comic"]["current_retry_count"] = 0
                _persist()

                comic_error: str | None = None
                comic_success = False
                try:
                    context = await _build_comic_chapter_regen_context(
                        project_id,
                        chapter_number,
                        db,
                        allow_empty=incremental_mode,
                        skip_existing_pages=incremental_mode,
                    )
                    if not context or not context["page_numbers"]:
                        if incremental_mode:
                            comic_success = True
                            chapter_result_entry["stage_results"]["comic"] = {
                                "status": "skipped",
                                "reason": "没有需要补充的漫画页面",
                                "skipped_existing_pages": context.get("skipped_existing_pages", []) if context else [],
                            }
                            task["stages"]["comic"]["error_message"] = None
                        else:
                            raise RuntimeError("该章节没有可重生成的漫画页面")
                    else:
                        context["db"] = db
                        comic_result = await _queue_comic_chapter_page_regen_tasks(
                            project_id,
                            user_id,
                            context,
                            run_inline=False,
                            max_concurrency=comic_page_concurrency,
                        )
                        for queued_task in comic_result.get("tasks", []):
                            if not isinstance(queued_task, dict):
                                continue
                            queued_page_number = queued_task.get("page_number")
                            if not isinstance(queued_page_number, int):
                                continue
                            comic_page_jobs.append(
                                asyncio.create_task(
                                    _run_pipeline_comic_page(
                                        chapter_number=chapter_number,
                                        page_number=queued_page_number,
                                        prompt_path=str(queued_task.get("prompt_path")) if queued_task.get("prompt_path") else None,
                                    )
                                )
                            )
                        chapter_result_entry["stage_results"]["comic"] = {
                            "status": "queued",
                            "queued_count": comic_result.get("queued_count", 0),
                            "page_numbers": [
                                queued_task.get("page_number")
                                for queued_task in comic_result.get("tasks", [])
                                if isinstance(queued_task, dict) and isinstance(queued_task.get("page_number"), int)
                            ],
                            "skipped_pages": comic_result.get("skipped_pages", []),
                            "skipped_existing_pages": context.get("skipped_existing_pages", []),
                            "concurrency": comic_result.get("concurrency"),
                        }
                        comic_success = True
                except Exception as exc:
                    comic_error = str(exc)
                    chapter_result_entry["errors"].append({
                        "stage": "comic",
                        "error": comic_error,
                    })
                    chapter_result_entry["stage_results"]["comic"] = {
                        "status": "failed",
                        "error": comic_error,
                    }
                    task["errors"].append({
                        "chapter_number": chapter_number,
                        "stage": "comic",
                        "error": comic_error,
                    })
                    task["stages"]["comic"]["failed"] = int(task["stages"]["comic"].get("failed") or 0) + 1
                    task["stages"]["comic"]["error_message"] = comic_error

                if comic_success:
                    task["stages"]["comic"]["succeeded"] = int(task["stages"]["comic"].get("succeeded") or 0) + 1
                    task["stages"]["comic"]["processed"] = int(task["stages"]["comic"].get("processed") or 0) + 1
                else:
                    task["stages"]["comic"]["processed"] = int(task["stages"]["comic"].get("processed") or 0) + 1
                    _mark_chapter_failed_once()

                chapter_result_entry["success"] = comic_success and not chapter_result_entry["errors"]
                if chapter_result_entry["success"]:
                    task["successful"] = int(task.get("successful") or 0) + 1
                task["completed"] = index
                task["chapter_results"].append(chapter_result_entry)
                task["current_stage"] = "chapter"
                task["updated_at"] = _utc_now_iso()
                _persist()

            if comic_page_jobs:
                task["current_stage"] = "comic"
                task["current_chapter_number"] = None
                task["current_retry_count"] = None
                task["stages"]["comic"]["current_chapter_number"] = None
                task["stages"]["comic"]["error_message"] = None
                task["updated_at"] = _utc_now_iso()
                _persist()
                await asyncio.gather(*comic_page_jobs)
                regen_state = _load_regen_state(project_id)
                for chapter_result_entry in task.get("chapter_results", []):
                    if not isinstance(chapter_result_entry, dict):
                        continue
                    comic_stage = chapter_result_entry.get("stage_results", {}).get("comic")
                    if not isinstance(comic_stage, dict) or comic_stage.get("status") not in {"queued", "completed"}:
                        continue
                    chapter_number_for_result = chapter_result_entry.get("chapter_number")
                    if not isinstance(chapter_number_for_result, int):
                        continue
                    page_numbers_for_result = [
                        page_number
                        for page_number in comic_stage.get("page_numbers", [])
                        if isinstance(page_number, int)
                    ]
                    page_statuses = [
                        (_latest_task_for_page(regen_state, chapter_number_for_result, page_number) or {}).get("status")
                        for page_number in page_numbers_for_result
                    ]
                    completed_pages = sum(1 for page_status in page_statuses if page_status == "completed")
                    failed_pages = sum(1 for page_status in page_statuses if page_status == "failed")
                    running_pages = sum(1 for page_status in page_statuses if page_status in {"queued", "running"})
                    comic_stage["page_status_summary"] = {
                        "total": len(page_numbers_for_result),
                        "completed": completed_pages,
                        "failed": failed_pages,
                        "running": running_pages,
                    }
                    if failed_pages > 0:
                        comic_stage["status"] = "partial_failed" if completed_pages > 0 else "failed"
                    elif page_numbers_for_result and completed_pages == len(page_numbers_for_result):
                        comic_stage["status"] = "completed"
                task["updated_at"] = _utc_now_iso()
                _persist()

            task["status"] = "completed"
            task["current_stage"] = None
            task["current_chapter_number"] = None
            task["current_retry_count"] = None
            task["completed"] = len(chapter_numbers)
            task["completed_at"] = _utc_now_iso()
            task["updated_at"] = _utc_now_iso()
            _persist()
    except Exception as exc:
        logger.error("全流程批量生成失败: project=%s task=%s error=%s", project_id, task_id, exc, exc_info=True)
        task["status"] = "failed"
        task["error_message"] = str(exc)
        task["completed_at"] = _utc_now_iso()
        task["updated_at"] = _utc_now_iso()
        _persist()


# ==================== 分镜脚本 AI 生成 ====================

_storyboard_gen_tasks: dict[str, dict[str, Any]] = {}
_comic_batch_tasks: dict[str, dict[str, Any]] = {}


class StoryboardGenerateRequest(BaseModel):
    target_pages: int = Field(default=10, ge=4, le=30)


class StoryboardBatchGenerateRequest(BaseModel):
    start_chapter_number: int = Field(..., ge=1)
    count: int = Field(..., ge=1)
    target_pages: int = Field(default=10, ge=4, le=30)


class ComicBatchGenerateRequest(BaseModel):
    start_chapter_number: int = Field(..., ge=1)
    count: int = Field(..., ge=1)
    comic_page_concurrency: int = Field(
        default=COMIC_PAGE_BATCH_CONCURRENCY_DEFAULT,
        ge=1,
        le=COMIC_PAGE_BATCH_CONCURRENCY_MAX,
        description="单章漫画页并发生成数",
    )


class ComicFullPipelineBatchGenerateRequest(BaseModel):
    start_chapter_number: int = Field(..., ge=1)
    count: int = Field(..., ge=1)
    style_id: int | None = Field(None, description="写作风格ID")
    target_word_count: int = Field(3000, ge=500, le=10000)
    enable_analysis: bool = Field(False, description="是否在章节生成后同步分析")
    enable_mcp: bool = Field(True, description="是否启用MCP工具")
    max_retries: int = Field(3, ge=0, le=5)
    model: str | None = Field(None, description="指定模型")
    target_pages: int = Field(10, ge=4, le=30)
    comic_page_concurrency: int = Field(
        default=COMIC_PAGE_BATCH_CONCURRENCY_DEFAULT,
        ge=1,
        le=COMIC_PAGE_BATCH_CONCURRENCY_MAX,
        description="漫画页生成阶段的单章并发数",
    )
    generation_mode: ComicFullPipelineGenerationMode = Field(
        default="incremental",
        description="生成模式：full=完整重建，incremental=增量补充",
    )


class ComicPipelineStageStatusResponse(BaseModel):
    total: int = 0
    processed: int = 0
    succeeded: int = 0
    failed: int = 0
    current_chapter_number: int | None = None
    current_retry_count: int | None = None
    error_message: str | None = None


class ComicFullPipelineBatchGenerateResponse(BaseModel):
    task_id: str
    status: str
    generation_mode: ComicFullPipelineGenerationMode
    total: int
    chapter_numbers: list[int]
    message: str


class ComicFullPipelineBatchStatusResponse(BaseModel):
    task_id: str
    project_id: str
    status: str
    generation_mode: ComicFullPipelineGenerationMode | None = None
    current_stage: str | None = None
    total: int
    completed: int
    successful: int
    failed: int
    chapter_numbers: list[int] = Field(default_factory=list)
    current_chapter_number: int | None = None
    current_retry_count: int | None = None
    stages: dict[str, ComicPipelineStageStatusResponse] = Field(default_factory=dict)
    errors: list[dict[str, Any]] = Field(default_factory=list)
    chapter_results: list[dict[str, Any]] = Field(default_factory=list)
    created_at: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    error_message: str | None = None


def _storyboard_json_to_markdown(json_data: dict[str, Any]) -> str:
    lines: list[str] = []
    pages = json_data.get("pages") or []
    for page in pages:
        page_num = page.get("page_number", "?")
        lines.append(f"### 第 {page_num} 页\n")
        panels = page.get("panels") or []
        for panel in panels:
            panel_num = panel.get("panel_number", "?")
            desc = panel.get("description", "")
            dialogue = panel.get("dialogue", "")
            scene = panel.get("scene", "")
            characters = panel.get("characters") or []
            camera = panel.get("camera_angle", "")
            emotion = panel.get("emotion", "")
            line_parts = [f"- **镜 {panel_num}**：{desc}"]
            if dialogue:
                line_parts.append(f"  - 对话：「{dialogue}」")
            if scene:
                line_parts.append(f"  - 场景：{scene}")
            if characters:
                line_parts.append(f"  - 角色：{'、'.join(characters)}")
            if camera:
                line_parts.append(f"  - 镜头：{camera}")
            if emotion:
                line_parts.append(f"  - 氛围：{emotion}")
            lines.append("\n".join(line_parts))
        lines.append("")
    return "\n".join(lines).strip()


def _split_system_prompt(template: str, default_system_prompt: str) -> tuple[str, str]:
    import re as _re

    system_prompt = default_system_prompt
    prompt_body = template
    sys_match = _re.search(r"<system>(.*?)</system>", template, _re.DOTALL)
    if sys_match:
        system_prompt = sys_match.group(1).strip()
        prompt_body = template[:sys_match.start()] + template[sys_match.end():]
    return system_prompt, prompt_body


def _storyboard_structure_issues(json_data: Any, target_pages: int) -> list[str]:
    issues: list[str] = []
    if not isinstance(json_data, dict):
        return [f"分镜输出必须是JSON对象，当前类型为{type(json_data).__name__}"]

    pages = json_data.get("pages")
    if not isinstance(pages, list) or not pages:
        return ["分镜 JSON 缺少 pages 数组或 pages 为空"]

    if len(pages) != target_pages:
        issues.append(f"pages 数量应为 {target_pages} 页，当前为 {len(pages)} 页")

    if not str(json_data.get("chapter_summary") or "").strip():
        issues.append("分镜 JSON 缺少 chapter_summary")

    continuity_rules = json_data.get("continuity_rules")
    if not isinstance(continuity_rules, list) or not any(str(rule).strip() for rule in continuity_rules):
        issues.append("分镜 JSON 缺少 continuity_rules 数组")

    for page_index, page in enumerate(pages, start=1):
        if not isinstance(page, dict):
            issues.append(f"第{page_index}页不是 JSON 对象")
            continue

        if page.get("page_number") != page_index:
            issues.append(f"第{page_index}页的 page_number 应为 {page_index}")

        panels = page.get("panels")
        if not isinstance(panels, list) or not panels:
            issues.append(f"第{page_index}页缺少 panels 数组")
            continue

        if len(panels) < 2 or len(panels) > 4:
            issues.append(f"第{page_index}页的 panels 数量应为 2-4 个，当前为 {len(panels)} 个")

        for panel_index, panel in enumerate(panels, start=1):
            if not isinstance(panel, dict):
                issues.append(f"第{page_index}页第{panel_index}格不是 JSON 对象")
                continue

            if panel.get("panel_number") != panel_index:
                issues.append(f"第{page_index}页第{panel_index}格的 panel_number 应为 {panel_index}")

            for field in ("description", "scene", "camera_angle", "emotion"):
                if not str(panel.get(field) or "").strip():
                    issues.append(f"第{page_index}页第{panel_index}格缺少 {field}")

            if not isinstance(panel.get("characters"), list):
                issues.append(f"第{page_index}页第{panel_index}格的 characters 必须是数组")

            if "dialogue" not in panel:
                issues.append(f"第{page_index}页第{panel_index}格缺少 dialogue 字段")

    return issues


def _normalize_storyboard_structure(json_data: dict[str, Any]) -> dict[str, Any]:
    normalized_pages: list[dict[str, Any]] = []
    pages = json_data.get("pages") if isinstance(json_data, dict) else []
    if not isinstance(pages, list):
        pages = []

    for page_index, page in enumerate(pages, start=1):
        if not isinstance(page, dict):
            continue

        normalized_panels: list[dict[str, Any]] = []
        panels = page.get("panels")
        if not isinstance(panels, list):
            panels = []

        for panel_index, panel in enumerate(panels, start=1):
            if not isinstance(panel, dict):
                continue

            characters = panel.get("characters")
            if isinstance(characters, list):
                normalized_characters = [str(item).strip() for item in characters if str(item).strip()]
            elif characters is None:
                normalized_characters = []
            else:
                normalized_characters = [str(characters).strip()] if str(characters).strip() else []

            normalized_panels.append({
                "panel_number": panel_index,
                "description": str(panel.get("description") or "").strip(),
                "dialogue": str(panel.get("dialogue") or "").strip(),
                "scene": str(panel.get("scene") or "").strip(),
                "characters": normalized_characters,
                "camera_angle": str(panel.get("camera_angle") or "").strip(),
                "emotion": str(panel.get("emotion") or "").strip(),
            })

        normalized_page: dict[str, Any] = {
            "page_number": page_index,
            "panels": normalized_panels,
        }
        for key in ("page_goal", "scene", "turning_point", "panel_count", "must_keep", "chapter_summary", "continuity_rules", "panel_plan"):
            if key in page:
                normalized_page[key] = page[key]
        normalized_pages.append(normalized_page)

    normalized_output: dict[str, Any] = {"pages": normalized_pages}
    for key in ("chapter_summary", "continuity_rules"):
        if key in json_data:
            normalized_output[key] = json_data[key]
    return normalized_output


def _parse_storyboard_ai_response(raw_response: Any) -> dict[str, Any]:
    if isinstance(raw_response, dict):
        content = raw_response.get("content")
    else:
        content = raw_response

    if isinstance(content, dict):
        json_data = content
    elif isinstance(content, list):
        json_data = {"pages": content}
    else:
        response_text = str(content or "").strip()
        if not response_text:
            finish_reason = raw_response.get("finish_reason") if isinstance(raw_response, dict) else None
            tool_calls = raw_response.get("tool_calls") if isinstance(raw_response, dict) else None
            raise RuntimeError(f"AI 返回内容为空, finish_reason={finish_reason}, tool_calls={tool_calls}")
        try:
            parsed = parse_json(response_text)
        except Exception as exc:
            preview = response_text[:500].replace("\n", "\\n")
            raise RuntimeError(f"AI 返回的分镜 JSON 解析失败: {exc}; 内容预览={preview}") from exc
        json_data = {"pages": parsed} if isinstance(parsed, list) else parsed

    if not isinstance(json_data, dict):
        raise RuntimeError(f"AI 返回的分镜数据类型错误: {type(json_data).__name__}")
    if not isinstance(json_data.get("pages"), list):
        raise RuntimeError("AI 返回的分镜数据缺少 pages 数组")
    return json_data


async def _generate_storyboard_for_chapter(
    *,
    project_id: str,
    chapter_number: int,
    user_id: str,
    target_pages: int,
    db: AsyncSession,
    ai_service: AIService,
) -> dict[str, Any]:
    from app.services.prompt_service import PromptService

    project_result = await db.execute(select(Project).where(Project.id == project_id))
    project = project_result.scalar_one_or_none()
    if not project:
        raise RuntimeError("项目不存在")

    chapter_result = await db.execute(
        select(Chapter).where(
            Chapter.project_id == project_id,
            Chapter.chapter_number == chapter_number,
        )
    )
    chapter = chapter_result.scalar_one_or_none()
    if not chapter or not chapter.content:
        raise RuntimeError(f"第{chapter_number}章不存在或没有内容")

    continuity_context = await _build_storyboard_continuity_context(
        project_id=project_id,
        chapter_number=chapter_number,
        db=db,
        project=project,
        chapter=chapter,
    )
    sanitized_chapter_content = _soften_sensitive_comic_text(chapter.content[:8000])
    sanitized_characters_info = _soften_sensitive_comic_text(continuity_context["characters_info"])
    sanitized_bridge_context = _soften_sensitive_comic_text(continuity_context["bridge_context"])
    sanitized_continuity_pack = _soften_sensitive_comic_text(continuity_context["continuity_pack"])
    image_text_rule = build_visible_text_rule(await _load_image_text_language(user_id, db), scope="comic")

    safety_rules = (
        "漫画脚本安全改写要求：如果章节里出现性爱、双修、挑逗或其他露骨亲密描写，"
        "必须改写成隐晦、诗意、非露骨的分镜表达，例如灵力交汇、灯影、衣袂、表情、距离感、氛围张力；"
        "不要输出裸露、性行为、身体私密部位、体液、挑逗动作等可视化描述。"
        f"\n{image_text_rule}"
    )
    if _SEXUAL_CONTENT_PATTERN.search(chapter.content or ""):
        safety_rules += (
            "\n角色年龄要求：在亲密场景中，所有角色必须呈现为成年人（至少18岁）。"
            "即使原文中有暗示年幼的描述，在涉及亲密描写的分镜中也必须将其描绘为成熟的成年女性。"
        )
    style_rules = (
        f"漫画风格统一要求：{_project_comic_style_instruction(project)} "
        "分镜脚本必须服务于该统一风格，后续页面生图会沿用同一风格。"
    )

    planning_template = await PromptService.get_template("STORYBOARD_PLANNING", user_id, db)
    planning_prompt = PromptService.format_prompt(
        planning_template,
        project_title=project.title or "未命名小说",
        genre=project.genre or "未设定",
        chapter_number=chapter_number,
        chapter_title=chapter.title or f"第{chapter_number}章",
        chapter_content=sanitized_chapter_content,
        characters_info=sanitized_characters_info,
        target_pages=str(target_pages),
        bridge_context=sanitized_bridge_context,
        continuity_pack=sanitized_continuity_pack,
        comic_style_instruction=_project_comic_style_instruction(project),
    ).strip()
    planning_prompt = f"{planning_prompt}\n\n{style_rules}\n\n{safety_rules}"
    planning_system_prompt, planning_body = _split_system_prompt(
        planning_prompt,
        "你是一位专业的漫画分镜总监。请严格按照JSON格式输出，不要输出任何其他内容。",
    )
    storyboard_plan = await ai_service.call_with_json_retry(
        prompt=planning_body,
        system_prompt=planning_system_prompt,
        max_retries=2,
        temperature=0.2,
        max_tokens=4000,
        auto_mcp=False,
        expected_type="object",
    )
    storyboard_plan_text = json.dumps(storyboard_plan, ensure_ascii=False, indent=2)

    generation_template = await PromptService.get_template("STORYBOARD_GENERATION", user_id, db)
    generation_prompt = PromptService.format_prompt(
        generation_template,
        project_title=project.title or "未命名小说",
        genre=project.genre or "未设定",
        chapter_number=chapter_number,
        chapter_title=chapter.title or f"第{chapter_number}章",
        chapter_content=sanitized_chapter_content,
        characters_info=sanitized_characters_info,
        target_pages=str(target_pages),
        storyboard_plan=storyboard_plan_text,
        bridge_context=sanitized_bridge_context,
        continuity_pack=sanitized_continuity_pack,
        comic_style_instruction=_project_comic_style_instruction(project),
    ).strip()
    generation_prompt = f"{generation_prompt}\n\n{style_rules}\n\n{safety_rules}"
    generation_system_prompt, generation_body = _split_system_prompt(
        generation_prompt,
        "你是一位专业的漫画分镜脚本编剧。请严格按照JSON格式输出分镜脚本，不要输出任何其他内容。",
    )

    repair_template = await PromptService.get_template("STORYBOARD_REPAIR", user_id, db)
    repair_prompt_base = PromptService.format_prompt(
        repair_template,
        chapter_content=sanitized_chapter_content,
        characters_info=sanitized_characters_info,
        storyboard_plan=storyboard_plan_text,
        current_storyboard="",
        validation_issues="",
        bridge_context=sanitized_bridge_context,
        continuity_pack=sanitized_continuity_pack,
    ).strip()
    repair_prompt_base = f"{repair_prompt_base}\n\n{style_rules}\n\n{safety_rules}"
    repair_system_prompt, repair_body_base = _split_system_prompt(
        repair_prompt_base,
        "你是一位专业的漫画分镜脚本修订编辑。请严格按照JSON格式输出，不要输出任何其他内容。",
    )

    def _render_repair_body(current_storyboard: str, validation_issues: list[str]) -> str:
        return PromptService.format_prompt(
            repair_template,
            chapter_content=sanitized_chapter_content,
            characters_info=sanitized_characters_info,
            storyboard_plan=storyboard_plan_text,
            current_storyboard=current_storyboard,
            validation_issues="\n".join(f"- {issue}" for issue in validation_issues),
            bridge_context=sanitized_bridge_context,
            continuity_pack=sanitized_continuity_pack,
        ).strip() + f"\n\n{style_rules}\n\n{safety_rules}"

    generation_attempts = [
        (
            "default",
            generation_body,
            generation_system_prompt,
        ),
        (
            "conservative",
            (
                f"{generation_body}\n\n"
                "重试模式：只返回合法 JSON。保持角色连续性、场景连续性和页面节奏完整。"
                "不要添加分析、解释、Markdown 或 JSON 以外的任何文字。"
            ),
            generation_system_prompt,
        ),
    ]

    last_error: str | None = None
    for attempt_name, prompt, current_system_prompt in generation_attempts:
        try:
            raw_response = await ai_service.generate_text(
                prompt=prompt,
                system_prompt=current_system_prompt,
                max_tokens=8000,
                auto_mcp=False,
            )
            content = raw_response.get("content") if isinstance(raw_response, dict) else raw_response
            logger.info(
                "分镜生成: AI返回 content类型=%s content长度=%d finish_reason=%s attempt=%s",
                type(content).__name__,
                len(content) if isinstance(content, str) else 0,
                raw_response.get("finish_reason") if isinstance(raw_response, dict) else "N/A",
                attempt_name,
            )
            json_data = _normalize_storyboard_structure(_parse_storyboard_ai_response(raw_response))
            issues = _storyboard_structure_issues(json_data, target_pages)
            if issues:
                raise RuntimeError("；".join(issues))
            markdown_content = _storyboard_json_to_markdown(json_data)
            normalized_json_text = json.dumps(json_data, ensure_ascii=False, indent=2)

            page_count = len(json_data.get("pages") or [])
            panel_count = sum(len(p.get("panels") or []) for p in (json_data.get("pages") or []))

            artifact = await _get_storyboard_artifact(project_id, chapter_number, db)
            if artifact is None:
                artifact = ComicStoryboardArtifact(
                    project_id=project_id,
                    chapter_number=chapter_number,
                )
                db.add(artifact)
            artifact.json_text = normalized_json_text
            artifact.markdown_content = markdown_content
            artifact.json_local_path = None
            artifact.markdown_local_path = None
            artifact.page_count = page_count
            artifact.panel_count = panel_count
            artifact.status = "completed"
            await db.commit()

            state_path = _project_state_write_path(project_id, "storyboard")
            state_data = _read_json_file(state_path) if state_path.is_file() else {}
            scripted = state_data.setdefault("scripted_chapters", {})
            scripted[str(chapter_number)] = {
                "status": "completed",
                "artifact_json": artifact.json_cos_url or None,
                "artifact_md": artifact.markdown_cos_url or None,
                "page_count": page_count,
                "panel_count": panel_count,
                "updated_at": _utc_now_iso(),
            }
            _write_json_file(state_path, state_data)

            return {
                "status": "completed",
                "page_count": page_count,
                "panel_count": panel_count,
                "json_path": artifact.json_cos_url or None,
                "md_path": artifact.markdown_cos_url or None,
                "attempt": attempt_name,
            }
        except Exception as exc:
            last_error = str(exc)
            logger.warning(
                "分镜生成失败，准备修复: project=%s chapter=%s attempt=%s error=%s",
                project_id,
                chapter_number,
                attempt_name,
                last_error,
                exc_info=True,
            )

    last_raw_storyboard = locals().get("raw_response")
    current_storyboard_text = ""
    if isinstance(last_raw_storyboard, dict):
        current_storyboard_text = str(last_raw_storyboard.get("content") or "")
    elif last_raw_storyboard is not None:
        current_storyboard_text = str(last_raw_storyboard)

    validation_issues = []
    try:
        current_json = _normalize_storyboard_structure(_parse_storyboard_ai_response(last_raw_storyboard))
        validation_issues = _storyboard_structure_issues(current_json, target_pages)
        current_storyboard_text = json.dumps(current_json, ensure_ascii=False, indent=2)
    except Exception as exc:
        validation_issues = [f"初次分镜输出无法解析为JSON: {exc}"]

    repair_attempts = [
        ("repair", _render_repair_body(current_storyboard_text, validation_issues)),
        (
            "repair_conservative",
            f"{_render_repair_body(current_storyboard_text, validation_issues)}\n\n"
            "重试模式：只返回合法 JSON。保留角色连续性和场景连续性。不要添加解释或 Markdown。",
        ),
    ]

    for attempt_name, prompt in repair_attempts:
        try:
            raw_response = await ai_service.generate_text(
                prompt=prompt,
                system_prompt=repair_system_prompt,
                max_tokens=8000,
                auto_mcp=False,
            )
            content = raw_response.get("content") if isinstance(raw_response, dict) else raw_response
            logger.info(
                "分镜修复: AI返回 content类型=%s content长度=%d finish_reason=%s attempt=%s",
                type(content).__name__,
                len(content) if isinstance(content, str) else 0,
                raw_response.get("finish_reason") if isinstance(raw_response, dict) else "N/A",
                attempt_name,
            )
            json_data = _normalize_storyboard_structure(_parse_storyboard_ai_response(raw_response))
            issues = _storyboard_structure_issues(json_data, target_pages)
            if issues:
                raise RuntimeError("；".join(issues))

            markdown_content = _storyboard_json_to_markdown(json_data)
            normalized_json_text = json.dumps(json_data, ensure_ascii=False, indent=2)
            page_count = len(json_data.get("pages") or [])
            panel_count = sum(len(p.get("panels") or []) for p in (json_data.get("pages") or []))

            artifact = await _get_storyboard_artifact(project_id, chapter_number, db)
            if artifact is None:
                artifact = ComicStoryboardArtifact(
                    project_id=project_id,
                    chapter_number=chapter_number,
                )
                db.add(artifact)
            artifact.json_text = normalized_json_text
            artifact.markdown_content = markdown_content
            artifact.json_local_path = None
            artifact.markdown_local_path = None
            artifact.page_count = page_count
            artifact.panel_count = panel_count
            artifact.status = "completed"
            await db.commit()

            state_path = _project_state_write_path(project_id, "storyboard")
            state_data = _read_json_file(state_path) if state_path.is_file() else {}
            scripted = state_data.setdefault("scripted_chapters", {})
            scripted[str(chapter_number)] = {
                "status": "completed",
                "artifact_json": artifact.json_cos_url or None,
                "artifact_md": artifact.markdown_cos_url or None,
                "page_count": page_count,
                "panel_count": panel_count,
                "updated_at": _utc_now_iso(),
            }
            _write_json_file(state_path, state_data)

            return {
                "status": "completed",
                "page_count": page_count,
                "panel_count": panel_count,
                "json_path": artifact.json_cos_url or None,
                "md_path": artifact.markdown_cos_url or None,
                "attempt": attempt_name,
            }
        except Exception as exc:
            last_error = str(exc)
            logger.warning(
                "分镜修复失败: project=%s chapter=%s attempt=%s error=%s",
                project_id,
                chapter_number,
                attempt_name,
                last_error,
                exc_info=True,
            )

    raise RuntimeError(last_error or "分镜生成失败")


async def _execute_storyboard_generation(
    task_id: str,
    project_id: str,
    chapter_number: int,
    user_id: str,
    target_pages: int,
) -> None:
    task = _storyboard_gen_tasks.get(task_id)
    if not task:
        return
    task["status"] = "running"
    task["updated_at"] = _utc_now_iso()

    try:
        from sqlalchemy.ext.asyncio import async_sessionmaker

        engine = await get_engine(user_id)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as db:
            ai_service = await _build_background_ai_service(user_id, db)
            result = await _generate_storyboard_for_chapter(
                project_id=project_id,
                chapter_number=chapter_number,
                user_id=user_id,
                target_pages=target_pages,
                db=db,
                ai_service=ai_service,
            )

        task["status"] = "completed"
        task["completed"] = 1
        task["updated_at"] = _utc_now_iso()
        task["result"] = {"page_count": result["page_count"], "panel_count": result["panel_count"]}
        logger.info("分镜生成完成: project=%s chapter=%s pages=%s panels=%s", project_id, chapter_number, result["page_count"], result["panel_count"])
    except Exception as exc:
        logger.error("分镜生成失败: project=%s chapter=%s error=%s", project_id, chapter_number, exc, exc_info=True)
        task["status"] = "failed"
        task["error"] = str(exc)
        task["updated_at"] = _utc_now_iso()


async def _execute_storyboard_batch_generation(
    task_id: str,
    project_id: str,
    chapter_numbers: list[int],
    user_id: str,
    target_pages: int,
) -> None:
    task = _storyboard_gen_tasks.get(task_id)
    if not task:
        return
    task["status"] = "running"
    task["updated_at"] = _utc_now_iso()

    for i, chapter_number in enumerate(chapter_numbers):
        task["current_chapter_number"] = chapter_number
        task["completed"] = i
        task["updated_at"] = _utc_now_iso()

        sub_task_id = str(uuid.uuid4())
        _storyboard_gen_tasks[sub_task_id] = {
            "task_id": sub_task_id,
            "project_id": project_id,
            "type": "single",
            "chapter_number": chapter_number,
            "status": "pending",
            "created_at": _utc_now_iso(),
            "updated_at": _utc_now_iso(),
        }
        await _execute_storyboard_generation(sub_task_id, project_id, chapter_number, user_id, target_pages)
        sub_task = _storyboard_gen_tasks.pop(sub_task_id, {})
        if sub_task.get("status") == "failed":
            task["errors"] = task.get("errors", [])
            task["errors"].append({"chapter_number": chapter_number, "error": sub_task.get("error", "")})

    task["completed"] = len(chapter_numbers)
    task["current_chapter_number"] = None
    task["status"] = "completed"
    task["updated_at"] = _utc_now_iso()
    logger.info("批量分镜生成完成: project=%s total=%s errors=%s", project_id, len(chapter_numbers), len(task.get("errors", [])))


@router.post("/projects/{project_id}/chapters/{chapter_number}/storyboard/generate", summary="AI生成章节分镜脚本")
async def generate_chapter_storyboard(
    project_id: str,
    chapter_number: int,
    request: Request,
    background_tasks: BackgroundTasks,
    payload: StoryboardGenerateRequest = StoryboardGenerateRequest(),
    db: AsyncSession = Depends(get_db),
):
    user_id = getattr(request.state, "user_id", None)
    await verify_project_access(project_id, user_id, db)

    result = await db.execute(select(Chapter).where(
        Chapter.project_id == project_id,
        Chapter.chapter_number == chapter_number,
    ))
    chapter = result.scalar_one_or_none()
    if not chapter:
        raise HTTPException(status_code=404, detail="章节不存在")
    if not chapter.content or not chapter.content.strip():
        raise HTTPException(status_code=400, detail="章节没有内容，请先生成或编写章节正文")

    task_id = str(uuid.uuid4())
    _storyboard_gen_tasks[task_id] = {
        "task_id": task_id,
        "project_id": project_id,
        "type": "single",
        "chapter_number": chapter_number,
        "status": "pending",
        "total": 1,
        "completed": 0,
        "current_chapter_number": chapter_number,
        "created_at": _utc_now_iso(),
        "updated_at": _utc_now_iso(),
    }
    background_tasks.add_task(
        _execute_storyboard_generation, task_id, project_id, chapter_number, user_id, payload.target_pages
    )
    return {"task_id": task_id, "status": "pending", "message": "分镜脚本生成任务已创建"}


@router.post("/projects/{project_id}/storyboard/batch-generate", summary="批量AI生成章节分镜脚本")
async def batch_generate_storyboard(
    project_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    payload: StoryboardBatchGenerateRequest = ...,
    db: AsyncSession = Depends(get_db),
):
    user_id = getattr(request.state, "user_id", None)
    await verify_project_access(project_id, user_id, db)

    result = await db.execute(
        select(Chapter)
        .where(Chapter.project_id == project_id)
        .order_by(Chapter.chapter_number)
    )
    all_chapters = result.scalars().all()
    end_number = payload.start_chapter_number + payload.count - 1
    chapters_to_generate = [
        ch for ch in all_chapters
        if payload.start_chapter_number <= ch.chapter_number <= end_number
        and ch.content and ch.content.strip()
    ]
    if not chapters_to_generate:
        raise HTTPException(status_code=400, detail="指定范围内没有有内容的章节")

    chapter_numbers = [ch.chapter_number for ch in chapters_to_generate]
    task_id = str(uuid.uuid4())
    _storyboard_gen_tasks[task_id] = {
        "task_id": task_id,
        "project_id": project_id,
        "type": "batch",
        "chapter_numbers": chapter_numbers,
        "status": "pending",
        "total": len(chapter_numbers),
        "completed": 0,
        "current_chapter_number": None,
        "errors": [],
        "created_at": _utc_now_iso(),
        "updated_at": _utc_now_iso(),
    }
    background_tasks.add_task(
        _execute_storyboard_batch_generation, task_id, project_id, chapter_numbers, user_id, payload.target_pages
    )
    return {
        "task_id": task_id,
        "status": "pending",
        "total": len(chapter_numbers),
        "chapter_numbers": chapter_numbers,
        "message": f"批量分镜生成任务已创建，共 {len(chapter_numbers)} 章",
    }


@router.get("/projects/{project_id}/storyboard/generate-status/{task_id}", summary="查询分镜生成任务状态")
async def get_storyboard_generate_status(
    project_id: str,
    task_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user_id = getattr(request.state, "user_id", None)
    await verify_project_access(project_id, user_id, db)

    task = _storyboard_gen_tasks.get(task_id)
    if not task or task.get("project_id") != project_id:
        raise HTTPException(status_code=404, detail="任务不存在")
    return task
