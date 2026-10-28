# Load .env into the environment of every recipe, so the app and libraries (OpenAI, LangChain…)
# see the same real env vars locally as on Railway. A missing .env is fine.
set dotenv-load

# bash (not sh): Railway's IaC SDK finds the CLI via bash's `$_` variable.
set shell := ["bash", "-cu"]

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

# Smoke-test a deployed environment, e.g. `just smoke https://api-production-xxxx.up.railway.app`
smoke base_url:
    SMOKE_BASE_URL={{base_url}} uv run pytest tests/smoke -q

# Check Railway matches .railway/railway.ts (exit 0 = in sync, 2 = changes pending)
infra-plan:
    railway config plan --detailed-exit-code

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
