"""merge backtest and app_config branches

Revision ID: 42b64b8b9499
Revises: a6dc4fc8e2ff, c1a2b3d4e5f6
Create Date: 2026-05-09 12:43:43.766073

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '42b64b8b9499'
down_revision: Union[str, None] = ('a6dc4fc8e2ff', 'c1a2b3d4e5f6')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
