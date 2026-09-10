"""drop min_probability from strategies

2026-05-28에 ai_probability를 폐기(검증 515건에서 LLM 확률과 실제 승률 무상관)한
뒤에도 strategies.min_probability 컬럼과 프론트 "최소확률" 입력이 남아 있었다.
executor는 이 값을 읽지 않고 Stage4 프롬프트에도 주입되지 않아, 사용자에게만
"확률 하한을 설정했다"는 착각을 주는 죽은 필드였다. 컬럼을 제거한다.

recommendations.ai_probability는 유지한다 — 폐기 이전 값이 들어있고 그 자체가
확률 무상관 분석의 원본 근거다 (신규 쓰기·API 노출은 없음, 동결 기록).

Revision ID: c3d4e5f6a7b9
Revises: b2c3d4e5f6a8
Create Date: 2026-09-10
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c3d4e5f6a7b9'
down_revision: Union[str, None] = 'b2c3d4e5f6a8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column('strategies', 'min_probability')


def downgrade() -> None:
    # 되돌릴 때는 기존 기본값(55)으로 채운다 — 어차피 읽는 코드가 없다.
    op.add_column(
        'strategies',
        sa.Column('min_probability', sa.Numeric(precision=5, scale=2),
                  nullable=False, server_default='55'),
    )
