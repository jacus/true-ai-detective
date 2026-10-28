# Load .env into the environment of every recipe, so the app and libraries (OpenAI, LangChain…)
# see the same real env vars locally as on Railway. A missing .env is fine.
set dotenv-load

# List recipes
default:
    @just --list

# Start Postgres and the API with auto-reload on http://localhost:8000
dev:
    docker compose up -d --wait db
    uv run fastapi dev src/detective/main.py

# Run all tests (e2e tests start their own throwaway Postgres)
test *args:
    uv run pytest {{args}}

# Lint, format check and type check
check:
    uv run ruff check .
    uv run ruff format --check .
    uv run ty check

# Shift Zero gate CLI from the installed Claude Code plugin (e.g. `just szero status`, `just szero gate`)
szero *args:
    python3 "$(ls -d ~/.claude/plugins/cache/shift-zero/shift-zero/*/ | sort -V | tail -1)gate/szero.py" {{args}}

# Stop local services (add -v to also wipe the database volume)
down *args:
    docker compose down {{args}}
