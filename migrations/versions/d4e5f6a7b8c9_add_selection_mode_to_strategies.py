"""add_selection_mode_to_strategies

Revision ID: d4e5f6a7b8c9
Revises: f3a2c1d0e9b8
Create Date: 2026-06-19 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'd4e5f6a7b8c9'
down_revision: Union[str, None] = 'f3a2c1d0e9b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('strategies',
        sa.Column('selection_mode', sa.String(length=20),
                  server_default='momentum', nullable=False)
    )


def downgrade() -> None:
    op.drop_column('strategies', 'selection_mode')
