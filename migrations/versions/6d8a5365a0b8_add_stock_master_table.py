"""add stock_master table

Revision ID: 6d8a5365a0b8
Revises: 85b15fe3da7b
Create Date: 2026-05-08 17:18:40.030749

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '6d8a5365a0b8'
down_revision: Union[str, None] = '85b15fe3da7b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 기존 하드코딩 시드 종목 코드 (b2907f846f64 마이그레이션에서 삽입된 20개)
_SEED_CODES = [
    "005930","000660","035420","035720","051910",
    "006400","068270","207940","005380","000270",
    "012330","028260","096770","017670","030200",
    "003550","034730","009540","010950","086790",
]


def upgrade() -> None:
    # stock_master 테이블 생성
    op.create_table(
        "stock_master",
        sa.Column("stock_id",   sa.Integer(),       autoincrement=True, nullable=False),
        sa.Column("stock_code", sa.String(20),      nullable=False),
        sa.Column("stock_name", sa.String(200),     nullable=False),
        sa.Column("market",     sa.String(20),      nullable=False),
        sa.Column("country",    sa.String(2),        nullable=False),
        sa.Column("sector",     sa.String(100),     nullable=True),
        sa.Column("is_active",  sa.Boolean(),       nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("stock_id"),
    )
    op.create_index("ix_stock_master_code_market", "stock_master",
                    ["stock_code", "market"], unique=True)
    op.create_index("ix_stock_master_name",   "stock_master", ["stock_name"])
    op.create_index("ix_stock_master_market", "stock_master", ["market"])

    # 하드코딩 시드 종목 제거 (자동 선별로 대체)
    op.execute(
        sa.text("DELETE FROM candidate_stocks WHERE stock_code = ANY(:codes)")
        .bindparams(codes=_SEED_CODES)
    )


def downgrade() -> None:
    op.drop_index("ix_stock_master_market",    table_name="stock_master")
    op.drop_index("ix_stock_master_name",      table_name="stock_master")
    op.drop_index("ix_stock_master_code_market", table_name="stock_master")
    op.drop_table("stock_master")
