"""add market outcome columns to recommendation_runs

Revision ID: f3a2c1d0e9b8
Revises: 5a4eb9b3c332
Create Date: 2026-05-15 20:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f3a2c1d0e9b8'
down_revision: Union[str, None] = '5a4eb9b3c332'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('recommendation_runs', sa.Column('kospi_at_run',    sa.Numeric(18, 2), nullable=True))
    op.add_column('recommendation_runs', sa.Column('kosdaq_at_run',   sa.Numeric(18, 2), nullable=True))
    op.add_column('recommendation_runs', sa.Column('kospi_change_1d', sa.Numeric(8, 4),  nullable=True))
    op.add_column('recommendation_runs', sa.Column('kosdaq_change_1d',sa.Numeric(8, 4),  nullable=True))
    op.add_column('recommendation_runs', sa.Column('verified_1d_at',  sa.DateTime(timezone=True), nullable=True))
    op.add_column('recommendation_runs', sa.Column('stage4_skipped',  sa.Boolean(), server_default='false', nullable=False))


def downgrade() -> None:
    op.drop_column('recommendation_runs', 'stage4_skipped')
    op.drop_column('recommendation_runs', 'verified_1d_at')
    op.drop_column('recommendation_runs', 'kosdaq_change_1d')
    op.drop_column('recommendation_runs', 'kospi_change_1d')
    op.drop_column('recommendation_runs', 'kosdaq_at_run')
    op.drop_column('recommendation_runs', 'kospi_at_run')
