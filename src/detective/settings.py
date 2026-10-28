from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Our own typed configuration, read from real environment variables.

    `.env` is loaded into the environment by `just` (locally) or provided by Railway (prod),
    never read here, so third-party libraries see exactly the same variables as we do.
    """

    # Default matches compose.yaml; Railway injects the real DATABASE_URL.
    database_url: str = "postgresql://detective:detective@localhost:5432/detective"
