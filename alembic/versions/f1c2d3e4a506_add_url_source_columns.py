"""add url source, language, glossary, stage columns (ADR 006)

Revision ID: f1c2d3e4a506
Revises: e7f3a1b2c905
Create Date: 2026-10-25 00:00:00.000000

"""
import sqlalchemy as sa
from alembic import op

revision = 'f1c2d3e4a506'
down_revision = 'e7f3a1b2c905'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("source_type", sa.String(16), nullable=False, server_default="file"))
    op.add_column("jobs", sa.Column("source_url", sa.Text(), nullable=True))
    op.add_column("jobs", sa.Column("language", sa.String(16), nullable=True))
    op.add_column("jobs", sa.Column("glossary", sa.Text(), nullable=True))
    op.add_column("jobs", sa.Column("stage", sa.String(32), nullable=True))


def downgrade() -> None:
    for column in ("stage", "glossary", "language", "source_url", "source_type"):
        op.drop_column("jobs", column)
