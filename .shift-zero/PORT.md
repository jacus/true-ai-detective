# Shift Zero port — True AI Detective

Ported: 2026-10-28 by Claude Code (claude-opus-5-5)

## Stack
- Python 3.14, uv, FastAPI (served with `fastapi run`/`fastapi dev`), psycopg 3, SQLAlchemy 2 + Alembic, pydantic-settings
- Postgres 18 + pgvector (`pgvector/pgvector:pg18`) via Docker Compose locally, testcontainers in tests
- Task runner: `just` (loads `.env` into the environment of every recipe; app code never reads `.env`)
- Next.js 16 + CopilotKit v2 in `web/` (npm, Biome, tsc)
- Interfaces: HTTP API (`api`), browser chat UI (`web`)

## Commands (all proven)
- setup:    `just setup` (once per clone: uv sync, npm ci, Playwright Chromium; WSL also needed `sudo playwright install-deps chromium`)
- boot:     `just dev` (Postgres via compose + API on :8000 with reload) and `just web` (Next.js on :3000)
- seed:     `just invite <email> <name> [owner|client]` prints a local invite link (the e2e tests use the same CLI)
- migrate:  `just dev` runs `alembic upgrade head` as the owner (`MIGRATIONS_DATABASE_URL`, `APP_DB_PASSWORD` defaults in Settings/justfile); new migration: `just migration "<msg>"`; Railway runs it pre-deploy
- e2e:      `just test` (each e2e session starts a throwaway pgvector container and real API processes)
- static:   `just check` (ruff lint, ruff format check, ty, biome, tsc)
- scoped:   `just test tests/e2e/test_health.py` · `just test -k <name>` · `just test path::test_name`
- smoke:    `just smoke https://api-production-fc37.up.railway.app https://web-production-7856b.up.railway.app` (deployed env; logs in as the smoke client from `.env` SMOKE_EMAIL/SMOKE_PASSWORD and resets its case first; two real `gpt-5.4-nano` calls incl. one via a real browser; Railway auto-deploys `main`, build+healthcheck ~4–5 min)
- remote:   `just invite-prod …` / `ssh railway-api '<cmd>'` (key + config set up via `railway ssh keys add` and `railway ssh config -s api`)
- infra:    `just infra-plan` (exit 0 = Railway matches `.railway/railway.ts`; apply with `railway config apply`)
- teardown: `just down` (add `-v` to wipe the DB volume)

## E2E surfaces
- HTTP: `tests/e2e/` (DB migrated with real Alembic as owner; api/CLI connect as `detective_app` like prod; `owner_database_url` fixture for setup that must bypass RLS; users created with the real CLI). The server is started as a subprocess with `fastapi run` (same as prod) and driven with httpx over a real socket. Model calls go to `tests/e2e/fake_openai.py` (OpenAI-compatible, streaming) via the SDK's `OPENAI_BASE_URL`; tests never make paid calls.
- Browser: `tests/e2e/test_web_chat.py` with pytest-playwright; the `web` fixture runs `next build` once per session and `next start` per test against the real api. Wait for `networkidle` before typing (chat must discover the agent first).
- Deployed: `tests/smoke/` (HTTP + browser), run via `just smoke <api-url> <web-url>`; not part of `just test`.

## Blockers
- none (DB backups deliberately deferred to S23 by the owner: hobby-level project for now)

## Testability debt
- LangGraph's checkpoint tables are created by the library (`PostgresSaver.setup()` inside an Alembic migration); after upgrading `langgraph-checkpoint-postgres`, add a migration that calls `setup()` again, or new library columns won't exist.
- `Settings()` is built at import time in `main.py`. Fine while e2e boots a real process with env vars; revisit if in-process tests become necessary.
