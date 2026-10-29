# Load .env into the environment of every recipe, so the app and libraries (OpenAI, LangChain…)
# see the same real env vars locally as on Railway. A missing .env is fine.
set dotenv-load

# bash (not sh): Railway's IaC SDK finds the CLI via bash's `$_` variable.
set shell := ["bash", "-cu"]

# Local-only password for the `detective_app` DB role (migrations create it). Railway sets a real secret.
export APP_DB_PASSWORD := env("APP_DB_PASSWORD", "detective_app")

# Local S3 (RustFS from compose.yaml), in boto3's own variables. Railway points them at the bucket.
export AWS_ENDPOINT_URL := env("AWS_ENDPOINT_URL", "http://localhost:9000")
export AWS_ACCESS_KEY_ID := env("AWS_ACCESS_KEY_ID", "detective")
export AWS_SECRET_ACCESS_KEY := env("AWS_SECRET_ACCESS_KEY", "detective-secret")
export AWS_DEFAULT_REGION := env("AWS_DEFAULT_REGION", "us-east-1")

# List recipes
default:
    @just --list

# One-time setup for a fresh clone: Python deps, web deps, Playwright's Chromium
setup:
    uv sync
    cd web && npm ci
    uv run playwright install chromium

# Start Postgres, S3 and the API with auto-reload on http://localhost:8000
dev:
    docker compose up -d --wait db s3
    uv run python -c "import boto3; s3 = boto3.client('s3'); 'evidence' in [b['Name'] for b in s3.list_buckets()['Buckets']] or s3.create_bucket(Bucket='evidence')"
    uv run alembic upgrade head
    uv run fastapi dev src/detective/main.py

# Create a migration from model changes (needs the local DB: `docker compose up -d db`)
migration message:
    uv run alembic upgrade head
    uv run alembic revision --autogenerate -m "{{message}}"

# Invite someone locally, e.g. `just invite me@example.com "My Name" owner`
invite email name role="client":
    uv run detective invite {{email}} --name "{{name}}" --role {{role}}

# Invite someone on Railway (runs the CLI inside the api container over SSH; needs `railway ssh config -s api`)
invite-prod email name role="client" web_url="https://web-production-7856b.up.railway.app":
    ssh -o BatchMode=yes railway-api 'detective invite {{email}} --name "{{name}}" --role {{role}} --base-url {{web_url}}' 

# Start the web chat with hot reload on http://localhost:3000 (talks to `just dev` on :8000)
web:
    cd web && npm run dev

# Run all tests (e2e tests start their own throwaway Postgres, fake OpenAI, api and web)
test *args:
    uv run pytest {{args}}

# Smoke-test a deployed environment (real nano calls), e.g. `just smoke https://api-… https://web-…`
smoke api_url web_url:
    SMOKE_API_URL={{api_url}} SMOKE_WEB_URL={{web_url}} \
    LANGSMITH_ENDPOINT=https://eu.api.smith.langchain.com LANGSMITH_PROJECT=true-ai-detective \
    uv run pytest tests/smoke -q

# Check Railway matches .railway/railway.ts (exit 0 = in sync, 2 = changes pending)
infra-plan:
    railway config plan --detailed-exit-code

# Lint, format check and type check (Python + web)
check:
    uv run ruff check .
    uv run ruff format --check .
    uv run ty check
    cd web && npx next typegen >/dev/null && npx biome check && npx tsc --noEmit

# Shift Zero gate CLI from the installed Claude Code plugin (e.g. `just szero status`, `just szero gate`)
szero *args:
    python3 "$(ls -d ~/.claude/plugins/cache/shift-zero/shift-zero/*/ | sort -V | tail -1)gate/szero.py" {{args}}

# Stop local services (add -v to also wipe the database volume)
down *args:
    docker compose down {{args}}
