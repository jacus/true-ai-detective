# Shift Zero port — True AI Detective

Ported: 2026-10-28 by Claude Code (claude-opus-5-5)

## Stack
- Python 3.14, uv, FastAPI (served with `fastapi run`/`fastapi dev`), psycopg 3, pydantic-settings
- Postgres 18 + pgvector (`pgvector/pgvector:pg18`) via Docker Compose locally, testcontainers in tests
- Task runner: `just` (loads `.env` into the environment of every recipe; app code never reads `.env`)
- Interfaces: HTTP API (browser UI arrives in S3)

## Commands (all proven)
- boot:     `just dev` (starts Postgres via compose, waits for healthy, runs API on :8000 with reload)
- seed:     n/a, no domain data yet (arrives with S4/S5)
- e2e:      `just test` (each e2e session starts a throwaway pgvector container and real API processes)
- static:   `just check` (ruff lint, ruff format check, ty)
- scoped:   `just test tests/e2e/test_health.py` · `just test -k <name>` · `just test path::test_name`
- smoke:    `just smoke https://api-production-fc37.up.railway.app` (deployed env; one real `gpt-5.4-nano` call; Railway auto-deploys `main`, build+healthcheck ~4–5 min)
- infra:    `just infra-plan` (exit 0 = Railway matches `.railway/railway.ts`; apply with `railway config apply`)
- teardown: `just down` (add `-v` to wipe the DB volume)

## E2E surfaces
- HTTP: `tests/e2e/`. The server is started as a subprocess with `fastapi run` (same as prod) and driven with httpx over a real socket. Model calls go to `tests/e2e/fake_openai.py` (OpenAI-compatible, streaming) via the SDK's `OPENAI_BASE_URL`; tests never make paid calls.
- HTTP (deployed): `tests/smoke/`, run via `just smoke <base-url>`; not part of `just test`.
- Browser: Playwright, to be added with the web UI (S3).

## Blockers
- none (DB backups deliberately deferred to S23 by the owner: hobby-level project for now)

## Testability debt
- `Settings()` is built at import time in `main.py`. Fine while e2e boots a real process with env vars; revisit if in-process tests become necessary.
