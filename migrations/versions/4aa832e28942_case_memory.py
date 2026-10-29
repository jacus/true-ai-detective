"""case memory

The case's searchable memory (RAG): facts and document chunks with their embeddings, in pgvector, plus a
full-text column for hybrid search. langchain-postgres owns the table layout; we let the library create it
the documented way (`PGEngine.init_vectorstore_table`, as the database owner, which also enables the
`vector` extension), then add foreign keys (deleted with their case and evidence), indexes, grants and RLS:
a row is visible only if its case is, and a new row's evidence must be in that same case.

No approximate (HNSW/IVFFlat) vector index, on purpose: searches always filter on one case, and an ANN index
finds the nearest rows of the whole table first and filters after, so a case's own rows get crowded out by
other cases' (recall silently drops as the table grows). The case_id index narrows a search to the case, and
Postgres then ranks that case's rows exactly. If cases ever hold millions of rows: HNSW with pgvector's
iterative scans (`hnsw.iterative_scan`).

Revision ID: 4aa832e28942
Revises: 819fdd53dd15
Create Date: 2026-10-29 13:39:24.855385

"""

from collections.abc import Sequence

from alembic import op
from langchain_postgres import Column, PGEngine
from langchain_postgres.v2.hybrid_search_config import HybridSearchConfig

from detective.settings import settings

# revision identifiers, used by Alembic.
revision: str = "4aa832e28942"
down_revision: str | Sequence[str] | None = "819fdd53dd15"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "case_memory"
OWN_CASE = "EXISTS (SELECT 1 FROM cases WHERE cases.id = case_id)"
SAME_CASE_EVIDENCE = (
    "EXISTS (SELECT 1 FROM evidence "
    "WHERE evidence.id = evidence_id AND evidence.case_id = case_memory.case_id)"
)


def upgrade() -> None:
    """Upgrade schema."""
    owner_url = settings.migrations_sqlalchemy_url.render_as_string(hide_password=False)
    engine = PGEngine.from_connection_string(owner_url)
    engine.init_vectorstore_table(
        TABLE,
        vector_size=1536,  # text-embedding-3-small
        metadata_columns=[
            Column("case_id", "UUID", nullable=False),
            Column("evidence_id", "UUID", nullable=False),
            Column("kind", "TEXT", nullable=False),  # fact | chunk
            Column("location", "TEXT"),  # page / part, when known
        ],
        hybrid_search_config=HybridSearchConfig(tsv_column="content_tsv"),
    )

    op.execute(f"ALTER TABLE {TABLE} ADD FOREIGN KEY (case_id) REFERENCES cases (id) ON DELETE CASCADE")
    op.execute(f"ALTER TABLE {TABLE} ADD FOREIGN KEY (evidence_id) REFERENCES evidence ON DELETE CASCADE")
    op.execute(f"CREATE INDEX ix_{TABLE}_case_id ON {TABLE} (case_id)")
    op.execute(f"CREATE INDEX ix_{TABLE}_content_tsv ON {TABLE} USING gin (content_tsv)")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {TABLE} TO detective_app")
    op.execute(f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {TABLE}_isolation ON {TABLE} USING ({OWN_CASE}) "
        f"WITH CHECK ({OWN_CASE} AND {SAME_CASE_EVIDENCE})"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute(f"DROP TABLE IF EXISTS {TABLE}")
