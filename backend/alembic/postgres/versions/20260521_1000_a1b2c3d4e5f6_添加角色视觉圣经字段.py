"""添加角色视觉圣经字段

Revision ID: a1b2c3d4e5f6
Revises: 6eb27fce64de
Create Date: 2026-05-21 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = '6eb27fce64de'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('characters', sa.Column('visual_bible', sa.JSON(), nullable=True, comment='角色视觉圣经JSON'))


def downgrade() -> None:
    op.drop_column('characters', 'visual_bible')
