"""添加图片文字语言配置

Revision ID: c4e5f6a7b8c9
Revises: 0b8a8a84d593
Create Date: 2026-06-08 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c4e5f6a7b8c9'
down_revision: Union[str, None] = '0b8a8a84d593'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'settings',
        sa.Column(
            'image_text_language',
            sa.String(length=20),
            nullable=False,
            server_default='zh',
            comment='图片文字语言: zh/en',
        ),
    )


def downgrade() -> None:
    op.drop_column('settings', 'image_text_language')
