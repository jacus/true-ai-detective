"""Case memory (RAG): a case's facts and document chunks, embedded in pgvector, for the detective to recall.

LangChain's interfaces, used directly: `init_embeddings` gives an `Embeddings`, langchain-postgres's
`PGVectorStore` is a `VectorStore` on our Postgres (the `case_memory` table, created by a migration), and
`as_retriever()` turns it into a retriever, which LangSmith traces as a `retriever` run with its documents.

Search is hybrid: vector similarity (meaning) and Postgres full text (exact words such as a name, an email
or a phone number), merged by reciprocal rank fusion. The full-text half uses `plainto_tsquery`, so it only
matches when every word of the query is in a text: it helps most with short, exact queries.

Tracing: embeddings aren't LangChain runs (no callbacks), so `index` and `forget` are traced with LangSmith's
`@traceable`: each shows up as its own run with what was embedded. A recall's embedding happens inside the
retriever run.

Isolation: the store runs on our SQLAlchemy engine, whose `begin` hook applies row-level security for the
request's user (`db.acting_user`), and every search also filters on the case.
"""

import uuid
from typing import Any, TypedDict

from langchain.embeddings import init_embeddings
from langchain_core.documents import Document
from langchain_core.vectorstores import VectorStoreRetriever
from langchain_postgres import PGEngine, PGVectorStore
from langchain_postgres.v2.hybrid_search_config import HybridSearchConfig, reciprocal_rank_fusion
from langsmith import traceable

from detective.db import engine
from detective.models import Evidence
from detective.settings import settings

TABLE = "case_memory"
RECALL = 8  # results per search, after fusion
CANDIDATES = 20  # taken from each half (vector, full text) before fusing

# Whole texts are sent, not tiktoken tokens (chunks are at most 4000 characters, far below the limit), so
# nothing needs tiktoken's encoding file, which it would otherwise download on first use.
embeddings = init_embeddings(settings.embedding_model, check_embedding_ctx_length=False)
_store: PGVectorStore | None = None


def hybrid(question: str = "") -> HybridSearchConfig:
    """A fresh hybrid-search config for one search, with that search's full-text query.

    Always a new object: langchain-postgres 0.0.19 writes the first query into a config it is given and then
    prefers that over any later query, so a config shared between searches (e.g. the store's own) would make
    every search in the process use the first question's words for its full-text half.
    """
    return HybridSearchConfig(
        tsv_column="content_tsv",
        fts_query=question,
        fusion_function=reciprocal_rank_fusion,
        primary_top_k=CANDIDATES,
        secondary_top_k=CANDIDATES,
    )


async def store() -> PGVectorStore:
    """The vector store, created on first use (it reads the table's columns once)."""
    global _store
    if _store is None:
        _store = await PGVectorStore.create(
            PGEngine.from_engine(engine),
            embedding_service=embeddings,
            table_name=TABLE,
            metadata_columns=["case_id", "evidence_id", "kind", "location"],
            hybrid_search_config=hybrid(),  # the store's own: inserts fill the full-text column from it
        )
    return _store


async def retriever() -> VectorStoreRetriever:
    return (await store()).as_retriever(search_kwargs={"k": RECALL})


class Chunk(TypedDict):
    text: str
    location: str | None


def _which(inputs: dict[str, Any]) -> dict[str, Any]:
    """Trace inputs: which evidence (not the whole database object)."""
    evidence: Evidence = inputs["evidence"]
    return {"evidence": evidence.label, "evidence_id": str(evidence.id), "case_id": str(evidence.case_id)}


def _embedded(documents: list[Document]) -> dict[str, Any]:
    """Trace outputs: the exact texts that were embedded and stored."""
    return {
        "count": len(documents),
        "documents": [
            {"kind": d.metadata["kind"], "location": d.metadata["location"], "text": d.page_content}
            for d in documents
        ],
    }


@traceable(name="index-evidence", process_inputs=_which, process_outputs=_embedded)
async def index(evidence: Evidence, chunks: list[Chunk]) -> list[Document]:
    """Embed and store an evidence's facts (with their quotes) and its text chunks; returns what it stored."""
    base = {"case_id": str(evidence.case_id), "evidence_id": str(evidence.id), "source": evidence.label}
    facts = [
        Document(
            fact.statement + (f' "{fact.quote}"' if fact.quote else ""),
            id=str(fact.id),
            metadata=base | {"kind": "fact", "location": fact.location},
        )
        for fact in evidence.facts
    ]
    parts = [
        Document(
            chunk["text"],
            id=str(uuid.uuid4()),
            metadata=base | {"kind": "chunk", "location": chunk["location"]},
        )
        for chunk in chunks
    ]
    if facts or parts:
        await (await store()).aadd_documents(facts + parts)  # embeds them (one call), then inserts the rows
    return facts + parts


@traceable(name="forget-evidence", process_inputs=_which)
async def forget(evidence: Evidence) -> None:
    """Remove an evidence's entries (before indexing it again)."""
    await (await store()).adelete(ids=None, filter={"evidence_id": {"$eq": str(evidence.id)}})


def cite(document: Document) -> str:
    """How a recalled entry is shown to the detective: the text and where it comes from."""
    where = ", ".join(filter(None, [document.metadata.get("source"), document.metadata.get("location")]))
    if document.metadata.get("kind") == "chunk":
        return f'Excerpt (from {where}): "{document.page_content[:1000]}"'
    return f"{document.page_content} (from {where})"
