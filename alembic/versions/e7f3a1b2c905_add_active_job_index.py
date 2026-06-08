"""add active job partial index

Revision ID: e7f3a1b2c905
Revises: d4a8b3c9f012
Create Date: 2026-06-07 00:00:00.000000

"""
from alembic import op

revision = 'e7f3a1b2c905'
down_revision = 'd4a8b3c9f012'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE INDEX ix_jobs_active_session
        ON jobs (session_id)
        WHERE status IN ('pending', 'queued', 'processing')
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_jobs_active_session")
