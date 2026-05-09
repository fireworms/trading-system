"""add app_config table

Revision ID: a6dc4fc8e2ff
Revises: 6787d280782e
Create Date: 2026-05-09 11:23:12.043296

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a6dc4fc8e2ff'
down_revision: Union[str, None] = '6787d280782e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'app_config',
        sa.Column('key', sa.String(100), primary_key=True),
        sa.Column('value_enc', sa.Text(), nullable=True),
        sa.Column('is_encrypted', sa.Boolean(), nullable=False, server_default='true'),
    )


def downgrade() -> None:
    op.drop_table('app_config')
