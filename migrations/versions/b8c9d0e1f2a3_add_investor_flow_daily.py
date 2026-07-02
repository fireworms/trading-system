"""add_investor_flow_daily

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-07-02 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision: str = 'b8c9d0e1f2a3'
down_revision: Union[str, None] = 'a7b8c9d0e1f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'investor_flow_daily',
        sa.Column('flow_id', UUID(as_uuid=True), primary_key=True),
        sa.Column('stock_code', sa.String(length=20), nullable=False),
        sa.Column('trade_date', sa.Date(), nullable=False),
        sa.Column('frgn_ntby_amt', sa.Numeric(18, 0), nullable=True),
        sa.Column('orgn_ntby_amt', sa.Numeric(18, 0), nullable=True),
        sa.Column('prsn_ntby_amt', sa.Numeric(18, 0), nullable=True),
        sa.Column('close', sa.Numeric(12, 0), nullable=True),
        sa.UniqueConstraint('stock_code', 'trade_date', name='uq_invflow_stock_date'),
    )
    op.create_index('ix_investor_flow_daily_stock_code', 'investor_flow_daily', ['stock_code'])


def downgrade() -> None:
    op.drop_index('ix_investor_flow_daily_stock_code', table_name='investor_flow_daily')
    op.drop_table('investor_flow_daily')
