"""cases with row level security

Creates the `detective_app` role the api connects as (no superuser, no BYPASSRLS), the `cases` table,
and the RLS policy that limits every query to the case of the user in `app.user_id`.
Needs APP_DB_PASSWORD in the environment (the app role's password).

Revision ID: efc4f563397a
Revises: 949e8dd753c7
Create Date: 2026-10-28 16:49:33.874425

"""

import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from psycopg import sql

# revision identifiers, used by Alembic.
revision: str = "efc4f563397a"
down_revision: str | Sequence[str] | None = "949e8dd753c7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The user the current transaction acts for; set by the api with set_config('app.user_id', ..., true).
CURRENT_USER_ID = "nullif(current_setting('app.user_id', true), '')::uuid"


def upgrade() -> None:
    """Upgrade schema."""
    # App role: idempotent, password from the environment (DDL can't take bound parameters, so quote safely).
    op.execute(
        "DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'detective_app') "
        "THEN CREATE ROLE detective_app; END IF; END $$"
    )
    password = sql.quote(os.environ["APP_DB_PASSWORD"])
    op.execute(
        f"ALTER ROLE detective_app WITH LOGIN NOSUPERUSER NOCREATEROLE NOBYPASSRLS PASSWORD {password}"
    )
    op.execute("GRANT USAGE ON SCHEMA public TO detective_app")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON users, invites, sessions TO detective_app")

    op.create_table(
        "cases",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("client_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["client_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("client_id"),
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON cases TO detective_app")
    # FORCE also applies the policy to the table owner; superusers still bypass, which is why the app
    # never connects as one.
    op.execute("ALTER TABLE cases ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE cases FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY cases_isolation ON cases "
        f"USING (client_id = {CURRENT_USER_ID}) WITH CHECK (client_id = {CURRENT_USER_ID})"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("cases")
    op.execute("REVOKE ALL ON users, invites, sessions FROM detective_app")
    op.execute("REVOKE USAGE ON SCHEMA public FROM detective_app")
    op.execute("DROP ROLE IF EXISTS detective_app")
