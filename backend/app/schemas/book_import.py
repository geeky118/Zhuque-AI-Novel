"""拆书导入相关的 Pydantic Schema"""
from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


TaskStatus = Literal["pending", "running", "completed", "failed", "cancelled"]
ImportMode = Literal["append", "overwrite"]
ExtractLevel = Literal["basic", "standard", "deep"]
WarningLevel = Literal["info", "warning", "error"]
BookImportExtractMode = Literal["tail", "full"]


class BookImportWarning(BaseModel):
    """导入告警信息"""
    code: str = Field(..., description="告警编码")
    message: str = Field(..., description="告警内容")
    level: WarningLevel = Field(default="warning", description="告警等级")


class ProjectSuggestion(BaseModel):
    """项目建议信息（可在预览页修改）"""
    title: str = Field(..., min_length=1, max_length=200, description="项目标题")
    description: Optional[str] = Field(None, description="项目简介")
    theme: Optional[str] = Field(None, description="主题")
    genre: Optional[str] = Field(None, description="类型")
    narrative_perspective: str = Field(default="第三人称", description="叙事视角")
    target_words: int = Field(default=100000, ge=1000, description="目标字数（默认10万字）")


class BookImportChapter(BaseModel):
    """预览章节"""
    title: str = Field(..., min_length=1, max_length=200, description="章节标题")
    content: str = Field(default="", description="章节正文")
    summary: Optional[str] = Field(None, description="章节摘要")
    chapter_number: int = Field(..., ge=1, description="章节序号")
    outline_title: Optional[str] = Field(None, description="关联大纲标题（可选）")


class BookImportOutline(BaseModel):
    """预览大纲"""
    title: str = Field(..., min_length=1, max_length=200, description="大纲标题")
    content: Optional[str] = Field(None, description="大纲内容")
    order_index: int = Field(..., ge=1, description="排序序号")
    structure: Optional[dict[str, Any]] = Field(None, description="结构化大纲（与系统大纲生成结构一致）")


class BookImportExtractedCharacter(BaseModel):
    """从TXT正文抽取的角色/人物设定"""
    name: str = Field(..., min_length=1, max_length=100, description="角色名")
    role_type: str = Field(default="supporting", description="角色定位")
    gender: Optional[str] = None
    age: Optional[str] = None
    personality: Optional[str] = None
    background: Optional[str] = None
    appearance: Optional[str] = None
    current_state: Optional[str] = None
    traits: list[str] = Field(default_factory=list)
    first_seen_chapter: Optional[int] = None
    importance: float = Field(default=0.5, ge=0, le=1)


class BookImportExtractedRelationship(BaseModel):
    """从TXT正文抽取的人物关系"""
    source: str = Field(..., min_length=1, max_length=100)
    target: str = Field(..., min_length=1, max_length=100)
    relationship_type: str = Field(default="关联")
    intimacy_level: int = Field(default=50, ge=-100, le=100)
    status: str = Field(default="active")
    description: Optional[str] = None


class BookImportExtractedOrganization(BaseModel):
    """从TXT正文抽取的组织/势力"""
    name: str = Field(..., min_length=1, max_length=100)
    organization_type: Optional[str] = None
    purpose: Optional[str] = None
    background: Optional[str] = None
    location: Optional[str] = None
    power_level: int = Field(default=50, ge=0, le=100)
    members: list[dict[str, Any]] = Field(default_factory=list)
    traits: list[str] = Field(default_factory=list)


class BookImportExtractedMemory(BaseModel):
    """从TXT正文抽取的重要剧情记忆"""
    title: str = Field(..., min_length=1, max_length=200)
    content: str = Field(..., min_length=1)
    memory_type: str = Field(default="plot_point")
    chapter_number: int = Field(default=1, ge=1)
    related_characters: list[str] = Field(default_factory=list)
    related_locations: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    importance_score: float = Field(default=0.5, ge=0, le=1)


class BookImportExtractedForeshadow(BaseModel):
    """从TXT正文抽取的伏笔/悬念"""
    title: str = Field(..., min_length=1, max_length=200)
    content: str = Field(..., min_length=1)
    plant_chapter_number: Optional[int] = Field(default=None, ge=1)
    target_resolve_chapter_number: Optional[int] = Field(default=None, ge=1)
    status: str = Field(default="planted")
    category: Optional[str] = None
    related_characters: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    importance: float = Field(default=0.5, ge=0, le=1)
    strength: int = Field(default=5, ge=1, le=10)
    subtlety: int = Field(default=5, ge=1, le=10)


class BookImportAnalysisDossier(BaseModel):
    """TXT深度拆解后的故事档案"""
    source_platform_notes: list[str] = Field(default_factory=list)
    world_notes: list[str] = Field(default_factory=list)
    characters: list[BookImportExtractedCharacter] = Field(default_factory=list)
    relationships: list[BookImportExtractedRelationship] = Field(default_factory=list)
    organizations: list[BookImportExtractedOrganization] = Field(default_factory=list)
    memories: list[BookImportExtractedMemory] = Field(default_factory=list)
    foreshadows: list[BookImportExtractedForeshadow] = Field(default_factory=list)


class BookImportTaskCreateRequest(BaseModel):
    """创建拆书任务请求"""
    extract_mode: BookImportExtractMode = Field(default="full", description="提取范围：tail=截取末章，full=整本")
    tail_chapter_count: int = Field(default=10, ge=5, le=9999, description="当 extract_mode=tail 时，截取末尾章节数；需为5的倍数，超过50将按整本处理")


class BookImportTaskCreateResponse(BaseModel):
    """创建任务响应"""
    task_id: str
    status: TaskStatus


class BookImportTaskStatusResponse(BaseModel):
    """任务状态响应"""
    task_id: str
    status: TaskStatus
    progress: int = Field(..., ge=0, le=100)
    message: Optional[str] = None
    error: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class BookImportPreviewResponse(BaseModel):
    """预览数据响应"""
    task_id: str
    project_suggestion: ProjectSuggestion
    chapters: list[BookImportChapter]
    outlines: list[BookImportOutline]
    warnings: list[BookImportWarning]
    analysis_dossier: BookImportAnalysisDossier = Field(default_factory=BookImportAnalysisDossier)


class BookImportApplyRequest(BaseModel):
    """确认导入请求（支持前端修订后的数据）"""
    project_suggestion: ProjectSuggestion
    chapters: list[BookImportChapter]
    outlines: list[BookImportOutline] = Field(default_factory=list)
    import_mode: ImportMode = Field(default="append", description="导入模式")


class BookImportApplyResponse(BaseModel):
    """确认导入响应"""
    success: bool
    project_id: str
    statistics: dict[str, int]
    warnings: list[BookImportWarning] = Field(default_factory=list)


class BookImportRetryRequest(BaseModel):
    """重试失败步骤请求"""
    steps: list[str] = Field(..., min_length=1, description="需要重试的步骤名列表，如 world_building / career_system / characters")
