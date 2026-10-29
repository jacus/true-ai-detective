from typing import Literal

from pydantic_settings import BaseSettings
from sqlalchemy.engine import URL, make_url


class Settings(BaseSettings):
    """Our own typed configuration, read from real environment variables.

    `.env` is loaded into the environment by `just` (locally) or provided by Railway (prod),
    never read here, so third-party libraries see exactly the same variables as we do.
    """

    # The app connects as `detective_app`: no superuser, no BYPASSRLS, so row-level security applies.
    # Defaults match compose.yaml (dev only); Railway injects the real URLs.
    database_url: str = "postgresql://detective_app:detective_app@localhost:5432/detective"
    # Migrations run as the database owner (creates tables, the app role and RLS policies).
    migrations_database_url: str = "postgresql://detective:detective@localhost:5432/detective"
    # `provider:model` for LangChain's init_chat_model. Very cheap models only (PLAN.md D3).
    chat_model: str = "openai:gpt-5.4-nano"
    # `provider:model` for LangChain's init_embeddings (case memory, S11). Its vectors are 1536 wide: the
    # case_memory table's size, so changing the model means a migration and a `detective reindex`.
    embedding_model: str = "openai:text-embedding-3-small"
    # The detective's next-step decision (S12): a decision model behind Vercel AI Gateway, called with the
    # OpenAI SDK's Decisions API (`decisions.create`); LangChain has no decision-model integration.
    decision_model: str = "typesafe-ai/jev"
    decisions_base_url: str = "https://ai-gateway.vercel.sh/v1"
    ai_gateway_api_key: str = ""  # secret, from the environment (.env locally, Railway in prod)
    # Session cookie is HTTPS-only. Browsers treat localhost as secure, so this stays on locally too.
    session_cookie_secure: bool = True
    session_days: int = 14
    # Evidence files: an S3 bucket (Railway Bucket in prod, RustFS from compose.yaml locally). boto3 reads its
    # own AWS_* variables (endpoint, keys, region); only what boto3 can't take from the environment is here.
    evidence_bucket: str = "evidence"
    s3_addressing_style: Literal["auto", "virtual", "path"] = "path"  # Railway Buckets: "virtual"
    evidence_max_bytes: int = 10 * 1024 * 1024

    @property
    def sqlalchemy_url(self) -> URL:
        """Same database, addressed for SQLAlchemy's async psycopg driver (Railway hands out `postgres://`)."""
        return make_url(self.database_url).set(drivername="postgresql+psycopg")

    @property
    def migrations_sqlalchemy_url(self) -> URL:
        return make_url(self.migrations_database_url).set(drivername="postgresql+psycopg")


settings = Settings()
