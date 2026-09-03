"""add daily_price (KRX 일별 전종목 시세 벌크 적재)

KIS는 종목당 1회 호출이라 전종목 히스토리 확보가 비현실적인데, KRX 오픈API는
하루치 전종목을 1회 호출로 준다 (KOSPI 943 + KOSDAQ 1822). 규칙 전략의
신고가 기간·거래대금 컷 파라미터를 백테스트로 튜닝할 때 쓴다.

복합 PK (stock_code, trade_date) — 재적재는 upsert.
KRX 제공 시작일 2010-01-04.

Revision ID: b2c3d4e5f6a8
Revises: a1b2c3d4e5f7
Create Date: 2026-09-03 20:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b2c3d4e5f6a8'
down_revision: Union[str, None] = 'a1b2c3d4e5f7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'daily_price',
        sa.Column('stock_code',    sa.String(20),     nullable=False),
        sa.Column('trade_date',    sa.Date(),         nullable=False),
        sa.Column('stock_name',    sa.String(100),    nullable=True),
        sa.Column('market',        sa.String(20),     nullable=True),
        sa.Column('sector_type',   sa.String(40),     nullable=True),
        sa.Column('open',          sa.Numeric(18, 2), nullable=True),
        sa.Column('high',          sa.Numeric(18, 2), nullable=True),
        sa.Column('low',           sa.Numeric(18, 2), nullable=True),
        sa.Column('close',         sa.Numeric(18, 2), nullable=True),
        sa.Column('change',        sa.Numeric(18, 2), nullable=True),
        sa.Column('change_pct',    sa.Numeric(10, 4), nullable=True),
        sa.Column('volume',        sa.Numeric(20, 0), nullable=True),
        sa.Column('trade_value',   sa.Numeric(24, 0), nullable=True),
        sa.Column('market_cap',    sa.Numeric(24, 0), nullable=True),
        sa.Column('listed_shares', sa.Numeric(20, 0), nullable=True),
        sa.PrimaryKeyConstraint('stock_code', 'trade_date'),
    )
    op.create_index('ix_daily_price_date', 'daily_price', ['trade_date'])
    op.create_index('ix_daily_price_date_value', 'daily_price', ['trade_date', 'trade_value'])


def downgrade() -> None:
    op.drop_index('ix_daily_price_date_value', table_name='daily_price')
    op.drop_index('ix_daily_price_date', table_name='daily_price')
    op.drop_table('daily_price')
