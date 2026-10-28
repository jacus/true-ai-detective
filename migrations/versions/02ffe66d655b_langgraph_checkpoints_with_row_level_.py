"""langgraph checkpoints with row level security

LangGraph's Postgres checkpointer owns its tables; we let the library create them the documented way
(`PostgresSaver.setup()`, as the database owner), then grant the app role access and add RLS: a checkpoint
row is visible only if its thread_id is a case the current user can see (and `cases` is itself RLS-filtered).
After upgrading langgraph-checkpoint-postgres, add a migration that calls setup() again.

Revision ID: 02ffe66d655b
Revises: efc4f563397a
Create Date: 2026-10-28 17:10:16.112981

"""

from collections.abc import Sequence

from alembic import op
from langgraph.checkpoint.postgres import PostgresSaver

from detective.settings import settings

# revision identifiers, used by Alembic.
revision: str = "02ffe66d655b"
down_revision: str | Sequence[str] | None = "efc4f563397a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CHECKPOINT_TABLES = ("checkpoints", "checkpoint_blobs", "checkpoint_writes")
OWN_CASE = "EXISTS (SELECT 1 FROM cases WHERE cases.id::text = thread_id)"


def upgrade() -> None:
    """Upgrade schema."""
    # The library's setup runs on its own connection (owner role); it is idempotent.
    with PostgresSaver.from_conn_string(settings.migrations_database_url) as saver:
        saver.setup()

    for table in CHECKPOINT_TABLES:
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO detective_app")
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY {table}_isolation ON {table} USING ({OWN_CASE}) WITH CHECK ({OWN_CASE})")
    op.execute("GRANT SELECT ON checkpoint_migrations TO detective_app")


def downgrade() -> None:
    """Downgrade schema."""
    for table in (*CHECKPOINT_TABLES, "checkpoint_migrations"):
        op.execute(f"DROP TABLE IF EXISTS {table}")
