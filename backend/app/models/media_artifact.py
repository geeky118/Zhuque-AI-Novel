"""媒体制品持久化模型。"""
from sqlalchemy import Column, String, Text, DateTime, Integer, ForeignKey, UniqueConstraint
from sqlalchemy.sql import func
from app.database import Base
import uuid


class CharacterImageArtifact(Base):
    """角色形象图状态与存储元数据。"""

    __tablename__ = "character_image_artifacts"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id = Column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    character_id = Column(String(36), ForeignKey("characters.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)

    prompt = Column(Text, nullable=False, default="", comment="角色形象提示词")
    status = Column(String(20), nullable=False, default="none", comment="状态: none/generating/ready/failed/capacity/policy")
    error = Column(Text, comment="最近一次失败原因")
    error_type = Column(String(50), comment="错误类型")
    file_name = Column(String(255), comment="本地文件名")
    local_path = Column(String(1000), comment="本地兼容文件路径")

    cos_bucket = Column(String(255), comment="COS Bucket")
    cos_region = Column(String(64), comment="COS Region")
    cos_object_key = Column(String(1000), comment="COS Object Key")
    cos_url = Column(String(2000), comment="COS 可访问 URL")
    cos_etag = Column(String(255), comment="COS ETag")
    content_type = Column(String(100), comment="媒体类型")
    content_length = Column(Integer, comment="对象大小")

    created_at = Column(DateTime, server_default=func.now(), comment="创建时间")
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), comment="更新时间")


class ComicStoryboardArtifact(Base):
    """章节分镜文本与存储元数据。"""

    __tablename__ = "comic_storyboard_artifacts"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id = Column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    chapter_id = Column(String(36), ForeignKey("chapters.id", ondelete="SET NULL"), nullable=True, index=True)
    chapter_number = Column(Integer, nullable=False, comment="章节号")

    status = Column(String(20), nullable=False, default="missing", comment="状态: missing/available/completed/edited")
    json_text = Column(Text, comment="分镜 JSON 文本")
    markdown_content = Column(Text, comment="分镜 Markdown 文本")
    json_local_path = Column(String(1000), comment="本地 JSON 路径")
    markdown_local_path = Column(String(1000), comment="本地 Markdown 路径")

    json_cos_bucket = Column(String(255), comment="JSON COS Bucket")
    json_cos_region = Column(String(64), comment="JSON COS Region")
    json_cos_object_key = Column(String(1000), comment="JSON COS Object Key")
    json_cos_url = Column(String(2000), comment="JSON COS URL")
    json_cos_etag = Column(String(255), comment="JSON COS ETag")
    json_content_length = Column(Integer, comment="JSON 对象大小")

    markdown_cos_bucket = Column(String(255), comment="Markdown COS Bucket")
    markdown_cos_region = Column(String(64), comment="Markdown COS Region")
    markdown_cos_object_key = Column(String(1000), comment="Markdown COS Object Key")
    markdown_cos_url = Column(String(2000), comment="Markdown COS URL")
    markdown_cos_etag = Column(String(255), comment="Markdown COS ETag")
    markdown_content_length = Column(Integer, comment="Markdown 对象大小")

    page_count = Column(Integer, comment="页数")
    panel_count = Column(Integer, comment="分镜格数")

    created_at = Column(DateTime, server_default=func.now(), comment="创建时间")
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), comment="更新时间")

    __table_args__ = (
        UniqueConstraint("project_id", "chapter_number", name="uq_comic_storyboard_project_chapter"),
    )


class ComicPageArtifact(Base):
    """漫画页图片、提示词与状态元数据。"""

    __tablename__ = "comic_page_artifacts"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id = Column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    chapter_id = Column(String(36), ForeignKey("chapters.id", ondelete="SET NULL"), nullable=True, index=True)
    chapter_number = Column(Integer, nullable=False, comment="章节号")
    page_number = Column(Integer, nullable=False, comment="页号")

    status = Column(String(20), nullable=False, default="missing", comment="状态: missing/ready/failed/queued/running/completed")
    prompt_text = Column(Text, comment="页面提示词文本")
    prompt_local_path = Column(String(1000), comment="本地提示词路径")
    image_local_path = Column(String(1000), comment="本地图片路径")

    prompt_cos_bucket = Column(String(255), comment="提示词 COS Bucket")
    prompt_cos_region = Column(String(64), comment="提示词 COS Region")
    prompt_cos_object_key = Column(String(1000), comment="提示词 COS Object Key")
    prompt_cos_url = Column(String(2000), comment="提示词 COS URL")
    prompt_cos_etag = Column(String(255), comment="提示词 COS ETag")
    prompt_content_length = Column(Integer, comment="提示词对象大小")

    image_cos_bucket = Column(String(255), comment="图片 COS Bucket")
    image_cos_region = Column(String(64), comment="图片 COS Region")
    image_cos_object_key = Column(String(1000), comment="图片 COS Object Key")
    image_cos_url = Column(String(2000), comment="图片 COS URL")
    image_cos_etag = Column(String(255), comment="图片 COS ETag")
    image_content_type = Column(String(100), comment="图片媒体类型")
    image_content_length = Column(Integer, comment="图片对象大小")

    failed_metadata = Column(Text, comment="失败元数据(JSON)")
    error_message = Column(Text, comment="最近一次错误信息")

    created_at = Column(DateTime, server_default=func.now(), comment="创建时间")
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), comment="更新时间")

    __table_args__ = (
        UniqueConstraint("project_id", "chapter_number", "page_number", name="uq_comic_page_project_chapter_page"),
    )
