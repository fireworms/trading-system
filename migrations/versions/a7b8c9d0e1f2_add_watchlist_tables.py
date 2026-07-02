"""add_watchlist_tables

Revision ID: a7b8c9d0e1f2
Revises: d4e5f6a7b8c9
Create Date: 2026-07-02 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision: str = 'a7b8c9d0e1f2'
down_revision: Union[str, None] = 'd4e5f6a7b8c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'watchlist_stocks',
        sa.Column('watch_id', UUID(as_uuid=True), primary_key=True),
        sa.Column('user_id', UUID(as_uuid=True),
                  sa.ForeignKey('users.user_id', ondelete='CASCADE'), nullable=False),
        sa.Column('stock_code', sa.String(length=20), nullable=False),
        sa.Column('stock_name', sa.String(length=100), nullable=False, server_default=''),
        sa.Column('sector', sa.String(length=100), nullable=True),
        sa.Column('memo', sa.Text(), nullable=False, server_default=''),
        sa.Column('added_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('user_id', 'stock_code', name='uq_watchlist_user_stock'),
    )
    op.create_table(
        'stock_analyses',
        sa.Column('analysis_id', UUID(as_uuid=True), primary_key=True),
        sa.Column('user_id', UUID(as_uuid=True),
                  sa.ForeignKey('users.user_id', ondelete='CASCADE'), nullable=False),
        sa.Column('stock_code', sa.String(length=20), nullable=False),
        sa.Column('stock_name', sa.String(length=100), nullable=False, server_default=''),
        sa.Column('analysis_date', sa.Date(), nullable=False),
        sa.Column('trigger_type', sa.String(length=30), nullable=False, server_default='manual'),
        sa.Column('gemini_model', sa.String(length=50), nullable=False, server_default=''),
        sa.Column('result', JSONB, nullable=True),
        sa.Column('input_snapshot', JSONB, nullable=True),
        sa.Column('raw_response', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_stock_analyses_stock_code', 'stock_analyses', ['stock_code'])
    op.create_index('ix_stock_analyses_user_stock_date', 'stock_analyses',
                    ['user_id', 'stock_code', 'analysis_date'])


def downgrade() -> None:
    op.drop_index('ix_stock_analyses_user_stock_date', table_name='stock_analyses')
    op.drop_index('ix_stock_analyses_stock_code', table_name='stock_analyses')
    op.drop_table('stock_analyses')
    op.drop_table('watchlist_stocks')
