"""add market regime columns to recommendation_runs

전략 성과가 종목 선정 탓인지 시장 국면 탓인지 분리해 보기 위한 기록 필드.
별도 전략을 만들지 않고 run 단위로 국면만 박아둔다 (같은 run의 픽들은
같은 날 같은 지수 상태이므로 픽 단위 저장은 중복).
기준을 나중에 바꿀 수 있도록 판정 결과(state)와 원본값(close/ma20)을 함께 저장.

Revision ID: a1b2c3d4e5f7
Revises: f7a8b9c0d1e2
Create Date: 2026-09-03 18:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a1b2c3d4e5f7'
down_revision: Union[str, None] = 'f7a8b9c0d1e2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('recommendation_runs', sa.Column('kospi_close',     sa.Numeric(18, 2), nullable=True))
    op.add_column('recommendation_runs', sa.Column('kospi_ma20',      sa.Numeric(18, 2), nullable=True))
    op.add_column('recommendation_runs', sa.Column('kospi_ma20_state', sa.String(10),    nullable=True))


def downgrade() -> None:
    op.drop_column('recommendation_runs', 'kospi_ma20_state')
    op.drop_column('recommendation_runs', 'kospi_ma20')
    op.drop_column('recommendation_runs', 'kospi_close')
