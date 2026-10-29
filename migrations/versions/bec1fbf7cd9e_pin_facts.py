"""pin facts

A map pin is a fact too (from now on the api adds one per pin): give the pins added before that their fact,
worded as the api words it. Their embeddings follow with `detective reindex`.

Revision ID: bec1fbf7cd9e
Revises: 358d2dfaca6a
Create Date: 2026-10-29 15:29:28.126554

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "bec1fbf7cd9e"
down_revision: str | Sequence[str] | None = "358d2dfaca6a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        """
        INSERT INTO facts (id, case_id, evidence_id, kind, statement)
        SELECT gen_random_uuid(), e.case_id, e.id, 'location',
               format('The client marked "%s" on the map at %s, %s', e.label,
                      to_char(e.latitude, 'FM990.00000'), to_char(e.longitude, 'FM990.00000'))
        FROM evidence e
        WHERE e.kind = 'pin' AND NOT EXISTS (SELECT 1 FROM facts f WHERE f.evidence_id = e.id)
        ORDER BY e.created_at
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute(
        "DELETE FROM facts USING evidence WHERE facts.evidence_id = evidence.id AND evidence.kind = 'pin'"
    )
