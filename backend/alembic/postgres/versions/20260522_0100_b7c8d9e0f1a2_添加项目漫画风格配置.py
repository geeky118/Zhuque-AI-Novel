"""添加项目漫画风格配置

Revision ID: b7c8d9e0f1a2
Revises: a1b2c3d4e5f6
Create Date: 2026-05-22 01:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7c8d9e0f1a2'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'projects',
        sa.Column(
            'comic_style',
            sa.String(length=50),
            nullable=False,
            server_default='guoman_refined',
            comment='项目统一漫画风格',
        ),
    )
    op.add_column(
        'projects',
        sa.Column('comic_style_prompt', sa.Text(), nullable=True, comment='自定义漫画风格提示词'),
    )
    op.alter_column('projects', 'comic_style', server_default=None)


def downgrade() -> None:
    op.drop_column('projects', 'comic_style_prompt')
    op.drop_column('projects', 'comic_style')
