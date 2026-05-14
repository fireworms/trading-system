"""add_use_trailing_stop_to_strategies

Revision ID: 9e846332aa6f
Revises: ef0a81524542
Create Date: 2026-05-14 14:51:16.311085

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '9e846332aa6f'
down_revision: Union[str, None] = 'ef0a81524542'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('strategies',
        sa.Column('use_trailing_stop', sa.Boolean(), server_default='false', nullable=False)
    )


def downgrade() -> None:
    op.drop_column('strategies', 'use_trailing_stop')
