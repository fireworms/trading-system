"""add virtual account support (AccountType.VIRTUAL + virtual_cash)

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-07-16
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'c9d0e1f2a3b4'
down_revision: Union[str, None] = 'b8c9d0e1f2a3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE account_type ADD VALUE IF NOT EXISTS 'VIRTUAL'")
    op.add_column("broker_accounts", sa.Column("virtual_cash", sa.Numeric(18, 2), nullable=True))
    op.add_column("broker_accounts", sa.Column("virtual_cash_initial", sa.Numeric(18, 2), nullable=True))


def downgrade() -> None:
    # PG enum 값 제거는 미지원 — 컬럼만 되돌린다
    op.drop_column("broker_accounts", "virtual_cash_initial")
    op.drop_column("broker_accounts", "virtual_cash")
