"""add_is_backtest_to_recommendation_runs

Revision ID: c1a2b3d4e5f6
Revises: 22f19c20aa7b
Create Date: 2026-05-09 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c1a2b3d4e5f6'
down_revision: Union[str, None] = '22f19c20aa7b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'recommendation_runs',
        sa.Column(
            'is_backtest',
            sa.Boolean(),
            nullable=False,
            server_default='false',
        ),
    )


def downgrade() -> None:
    op.drop_column('recommendation_runs', 'is_backtest')
