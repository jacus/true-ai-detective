# Shift Zero port — True AI Detective

Ported: 2026-10-28 by Claude Code (claude-opus-5-5)

## Stack
- Python 3.14, uv, FastAPI (served with `fastapi run`/`fastapi dev`), psycopg 3, pydantic-settings
- Postgres 18 + pgvector (`pgvector/pgvector:pg18`) via Docker Compose locally, testcontainers in tests
- Task runner: `just` (loads `.env` into the environment of every recipe; app code never reads `.env`)
- Next.js 16 + CopilotKit v2 in `web/` (npm, Biome, tsc)
- Interfaces: HTTP API (`api`), browser chat UI (`web`)

## Commands (all proven)
- setup:    `just setup` (once per clone: uv sync, npm ci, Playwright Chromium; WSL also needed `sudo playwright install-deps chromium`)
- boot:     `just dev` (Postgres via compose + API on :8000 with reload) and `just web` (Next.js on :3000)
- seed:     n/a, no domain data yet (arrives with S4/S5)
- e2e:      `just test` (each e2e session starts a throwaway pgvector container and real API processes)
- static:   `just check` (ruff lint, ruff format check, ty, biome, tsc)
- scoped:   `just test tests/e2e/test_health.py` · `just test -k <name>` · `just test path::test_name`
- smoke:    `just smoke https://<your-api>.up.railway.app https://<your-web>.up.railway.app` (deployed env; two real `gpt-5.4-nano` calls incl. one via a real browser; Railway auto-deploys `main`, build+healthcheck ~4–5 min)
- infra:    `just infra-plan` (exit 0 = Railway matches `.railway/railway.ts`; apply with `railway config apply`)
- teardown: `just down` (add `-v` to wipe the DB volume)

## E2E surfaces
- HTTP: `tests/e2e/`. The server is started as a subprocess with `fastapi run` (same as prod) and driven with httpx over a real socket. Model calls go to `tests/e2e/fake_openai.py` (OpenAI-compatible, streaming) via the SDK's `OPENAI_BASE_URL`; tests never make paid calls.
- Browser: `tests/e2e/test_web_chat.py` with pytest-playwright; the `web` fixture runs `next build` once per session and `next start` per test against the real api. Wait for `networkidle` before typing (chat must discover the agent first).
- Deployed: `tests/smoke/` (HTTP + browser), run via `just smoke <api-url> <web-url>`; not part of `just test`.

## Blockers
- none (DB backups deliberately deferred to S23 by the owner: hobby-level project for now)

## Testability debt
- `Settings()` is built at import time in `main.py`. Fine while e2e boots a real process with env vars; revisit if in-process tests become necessary.
