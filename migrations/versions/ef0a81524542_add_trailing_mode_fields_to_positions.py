"""add_trailing_mode_fields_to_positions

Revision ID: ef0a81524542
Revises: b0b705826d79
Create Date: 2026-05-12 11:31:48.757294

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'ef0a81524542'
down_revision: Union[str, None] = 'b0b705826d79'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('positions', sa.Column('target_hit_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('positions', sa.Column('target_hit_peak', sa.Numeric(precision=18, scale=0), nullable=True))


def downgrade() -> None:
    op.drop_column('positions', 'target_hit_peak')
    op.drop_column('positions', 'target_hit_at')
