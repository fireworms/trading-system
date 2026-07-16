"""add condition_status to stock_analyses (무효화_조건 자동 체크 상태)

Revision ID: e5f6a7b8c9d0
Revises: c9d0e1f2a3b4
Create Date: 2026-07-16
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = 'e5f6a7b8c9d0'
down_revision: Union[str, None] = 'c9d0e1f2a3b4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("stock_analyses", sa.Column("condition_status", JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column("stock_analyses", "condition_status")
