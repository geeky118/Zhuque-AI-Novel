"""新增漫画与角色制品存储表

Revision ID: 0b8a8a84d593
Revises: 6ff45db05863
Create Date: 2026-04-27 17:02:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0b8a8a84d593"
down_revision: Union[str, None] = "6ff45db05863"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "character_image_artifacts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("character_id", sa.String(length=36), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("error_type", sa.String(length=50), nullable=True),
        sa.Column("file_name", sa.String(length=255), nullable=True),
        sa.Column("local_path", sa.String(length=1000), nullable=True),
        sa.Column("cos_bucket", sa.String(length=255), nullable=True),
        sa.Column("cos_region", sa.String(length=64), nullable=True),
        sa.Column("cos_object_key", sa.String(length=1000), nullable=True),
        sa.Column("cos_url", sa.String(length=2000), nullable=True),
        sa.Column("cos_etag", sa.String(length=255), nullable=True),
        sa.Column("content_type", sa.String(length=100), nullable=True),
        sa.Column("content_length", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
        sa.ForeignKeyConstraint(["character_id"], ["characters.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("character_id"),
    )
    op.create_index(op.f("ix_character_image_artifacts_character_id"), "character_image_artifacts", ["character_id"], unique=True)
    op.create_index(op.f("ix_character_image_artifacts_project_id"), "character_image_artifacts", ["project_id"], unique=False)

    op.create_table(
        "comic_storyboard_artifacts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("chapter_id", sa.String(length=36), nullable=True),
        sa.Column("chapter_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("json_text", sa.Text(), nullable=True),
        sa.Column("markdown_content", sa.Text(), nullable=True),
        sa.Column("json_local_path", sa.String(length=1000), nullable=True),
        sa.Column("markdown_local_path", sa.String(length=1000), nullable=True),
        sa.Column("json_cos_bucket", sa.String(length=255), nullable=True),
        sa.Column("json_cos_region", sa.String(length=64), nullable=True),
        sa.Column("json_cos_object_key", sa.String(length=1000), nullable=True),
        sa.Column("json_cos_url", sa.String(length=2000), nullable=True),
        sa.Column("json_cos_etag", sa.String(length=255), nullable=True),
        sa.Column("json_content_length", sa.Integer(), nullable=True),
        sa.Column("markdown_cos_bucket", sa.String(length=255), nullable=True),
        sa.Column("markdown_cos_region", sa.String(length=64), nullable=True),
        sa.Column("markdown_cos_object_key", sa.String(length=1000), nullable=True),
        sa.Column("markdown_cos_url", sa.String(length=2000), nullable=True),
        sa.Column("markdown_cos_etag", sa.String(length=255), nullable=True),
        sa.Column("markdown_content_length", sa.Integer(), nullable=True),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("panel_count", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
        sa.ForeignKeyConstraint(["chapter_id"], ["chapters.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "chapter_number", name="uq_comic_storyboard_project_chapter"),
    )
    op.create_index(op.f("ix_comic_storyboard_artifacts_chapter_id"), "comic_storyboard_artifacts", ["chapter_id"], unique=False)
    op.create_index(op.f("ix_comic_storyboard_artifacts_project_id"), "comic_storyboard_artifacts", ["project_id"], unique=False)

    op.create_table(
        "comic_page_artifacts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("chapter_id", sa.String(length=36), nullable=True),
        sa.Column("chapter_number", sa.Integer(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("prompt_text", sa.Text(), nullable=True),
        sa.Column("prompt_local_path", sa.String(length=1000), nullable=True),
        sa.Column("image_local_path", sa.String(length=1000), nullable=True),
        sa.Column("prompt_cos_bucket", sa.String(length=255), nullable=True),
        sa.Column("prompt_cos_region", sa.String(length=64), nullable=True),
        sa.Column("prompt_cos_object_key", sa.String(length=1000), nullable=True),
        sa.Column("prompt_cos_url", sa.String(length=2000), nullable=True),
        sa.Column("prompt_cos_etag", sa.String(length=255), nullable=True),
        sa.Column("prompt_content_length", sa.Integer(), nullable=True),
        sa.Column("image_cos_bucket", sa.String(length=255), nullable=True),
        sa.Column("image_cos_region", sa.String(length=64), nullable=True),
        sa.Column("image_cos_object_key", sa.String(length=1000), nullable=True),
        sa.Column("image_cos_url", sa.String(length=2000), nullable=True),
        sa.Column("image_cos_etag", sa.String(length=255), nullable=True),
        sa.Column("image_content_type", sa.String(length=100), nullable=True),
        sa.Column("image_content_length", sa.Integer(), nullable=True),
        sa.Column("failed_metadata", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
        sa.ForeignKeyConstraint(["chapter_id"], ["chapters.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "chapter_number", "page_number", name="uq_comic_page_project_chapter_page"),
    )
    op.create_index(op.f("ix_comic_page_artifacts_chapter_id"), "comic_page_artifacts", ["chapter_id"], unique=False)
    op.create_index(op.f("ix_comic_page_artifacts_project_id"), "comic_page_artifacts", ["project_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_comic_page_artifacts_project_id"), table_name="comic_page_artifacts")
    op.drop_index(op.f("ix_comic_page_artifacts_chapter_id"), table_name="comic_page_artifacts")
    op.drop_table("comic_page_artifacts")

    op.drop_index(op.f("ix_comic_storyboard_artifacts_project_id"), table_name="comic_storyboard_artifacts")
    op.drop_index(op.f("ix_comic_storyboard_artifacts_chapter_id"), table_name="comic_storyboard_artifacts")
    op.drop_table("comic_storyboard_artifacts")

    op.drop_index(op.f("ix_character_image_artifacts_project_id"), table_name="character_image_artifacts")
    op.drop_index(op.f("ix_character_image_artifacts_character_id"), table_name="character_image_artifacts")
    op.drop_table("character_image_artifacts")
