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
    # Session cookie is HTTPS-only. Browsers treat localhost as secure, so this stays on locally too.
    session_cookie_secure: bool = True
    session_days: int = 14

    @property
    def sqlalchemy_url(self) -> URL:
        """Same database, addressed for SQLAlchemy's async psycopg driver (Railway hands out `postgres://`)."""
        return make_url(self.database_url).set(drivername="postgresql+psycopg")

    @property
    def migrations_sqlalchemy_url(self) -> URL:
        return make_url(self.migrations_database_url).set(drivername="postgresql+psycopg")


settings = Settings()
