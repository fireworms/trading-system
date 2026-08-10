"""add research_notes (AI 리서치 탭 — 자유 질문 종목 리서치)

Revision ID: f7a8b9c0d1e2
Revises: e5f6a7b8c9d0
Create Date: 2026-08-10
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision: str = 'f7a8b9c0d1e2'
down_revision: Union[str, None] = 'e5f6a7b8c9d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "research_notes",
        sa.Column("research_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True),
                  sa.ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False),
        sa.Column("stock_code", sa.String(20), nullable=False),
        sa.Column("stock_name", sa.String(100), nullable=False, server_default=""),
        sa.Column("question", sa.Text, nullable=False),
        sa.Column("answer_md", sa.Text, nullable=False),
        sa.Column("gemini_model", sa.String(50), nullable=False, server_default=""),
        sa.Column("sources", JSONB, nullable=True),
        sa.Column("input_snapshot", JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_research_notes_user_id", "research_notes", ["user_id"])
    op.create_index("ix_research_notes_stock_code", "research_notes", ["stock_code"])


def downgrade() -> None:
    op.drop_index("ix_research_notes_stock_code", table_name="research_notes")
    op.drop_index("ix_research_notes_user_id", table_name="research_notes")
    op.drop_table("research_notes")
