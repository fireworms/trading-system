"""drop_candidate_stocks_add_current_price_at_rec

Revision ID: 22f19c20aa7b
Revises: 9805532bafc0
Create Date: 2026-05-08 22:43:06.056308

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '22f19c20aa7b'
down_revision: Union[str, None] = '9805532bafc0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('recommendations', sa.Column(
        'current_price_at_rec', sa.Numeric(precision=18, scale=4), nullable=True
    ))
    op.drop_table('candidate_stocks')


def downgrade() -> None:
    op.drop_column('recommendations', 'current_price_at_rec')
    # candidate_stocks는 downgrade 시 재생성하지 않음 (대체됨)
