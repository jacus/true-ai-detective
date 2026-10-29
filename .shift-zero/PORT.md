# Shift Zero port — True AI Detective

Ported: 2026-10-28 by Claude Code (claude-opus-5-5)

## Stack
- Python 3.14, uv, FastAPI (served with `fastapi run`/`fastapi dev`), psycopg 3, SQLAlchemy 2 + Alembic, pydantic-settings
- Postgres 18 + pgvector (`pgvector/pgvector:pg18`) and S3 (RustFS `rustfs/rustfs:1.0.4`; Railway Bucket in prod) via Docker Compose locally, testcontainers in tests
- Task runner: `just` (loads `.env` into the environment of every recipe; app code never reads `.env`)
- Next.js 16 + CopilotKit v2 in `web/` (npm, Biome, tsc)
- Interfaces: HTTP API (`api`), browser chat UI (`web`)

## Commands (all proven)
- setup:    `just setup` (once per clone: uv sync, npm ci, Playwright Chromium; WSL also needed `sudo playwright install-deps chromium`)
- boot:     `just dev` (Postgres + RustFS via compose; `docker compose up -d searxng` for web search on :8888, creates the `evidence` bucket, API on :8000 with reload; RustFS console :9001) and `just web` (Next.js on :3000)
- seed:     `just invite <email> <name> [owner|client]` prints a local invite link (the e2e tests use the same CLI)
- migrate:  `just dev` runs `alembic upgrade head` as the owner (`MIGRATIONS_DATABASE_URL`, `APP_DB_PASSWORD` defaults in Settings/justfile); new migration: `just migration "<msg>"`; Railway runs it pre-deploy
- e2e:      `just test` (each e2e session starts throwaway pgvector + RustFS containers and real API processes)
- static:   `just check` (ruff lint, ruff format check, ty, biome, tsc)
- scoped:   `just test tests/e2e/test_health.py` · `just test -k <name>` · `just test path::test_name`
- smoke:    `just smoke https://<your-api>.up.railway.app https://<your-web>.up.railway.app` (deployed env; logs in as the smoke client from `.env` SMOKE_EMAIL/SMOKE_PASSWORD and resets its case first; real `gpt-5.4-nano` calls (chat, known photo, the 2-page witness PDF, example.com, one via a real browser; cents at most); reads the run's trace back from LangSmith EU with `langsmith.Client` (key from `.env`, endpoint/project set by the recipe); Railway auto-deploys `main`, build+healthcheck ~4–5 min)
- remote:   `just invite-prod …` / `ssh railway-api '<cmd>'` (key + config set up via `railway ssh keys add` and `railway ssh config -s api`)
- infra:    `just infra-plan` (exit 0 = Railway matches `.railway/railway.ts`; apply with `railway config apply`)
- reindex:  `just reindex` / `just reindex-prod` (rebuild the case memory; e2e runs the CLI with `app_env(...)` + `MIGRATIONS_DATABASE_URL`)
- record:   `just record-research` (re-records `tests/e2e/cassettes/research.yaml` from the real web and compose's SearXNG; vcrpy `new_episodes`; review the cassette diff before committing)
- graphs:   `just graphs` (compiles each LangGraph graph and draws it to `docs/graphs/*.png` via mermaid.ink)
- teardown: `just down` (add `-v` to wipe the DB volume)

## E2E surfaces
- HTTP: `tests/e2e/` (DB migrated with real Alembic as owner; api/CLI connect as `detective_app` like prod; `owner_database_url` fixture for setup that must bypass RLS; users created with the real CLI). The server is started as a subprocess with `fastapi run` (same as prod) and driven with httpx over a real socket. Evidence files go to a throwaway RustFS bucket (`s3_endpoint` fixture; `storage()` inspects it from outside). Model calls go to `tests/e2e/fake_openai.py` (`chat_request()` = the last streamed request, i.e. the chat reply; `run_agent(..., None)` = a run without a new message) (OpenAI-compatible, streaming) via the SDK's `OPENAI_BASE_URL`; tests never make paid calls. Non-streamed requests are structured output, answered per schema name: `PhotoFacts` → `PHOTO_SIGHTINGS`, `DocumentFacts` → one fact quoting the chunk's first sentence, `CaseBoard` → a timeline entry per numbered fact and a lead for the newest; `/v1/embeddings` hashes words into 1536-d unit vectors (texts sharing words are close), a request offering tools gets tool calls (a `search_case_file` call unless a test queued rounds via `POST /tool_calls`), `/v1/decisions` (OpenAI Decisions API, standing in for JEV via AI Gateway) decides `ask` unless a test queued decisions via `POST /decisions` (`{"choice": …, "probabilities": …}` or `{"refusal": true}`), or what a test queued via `POST /answers/{schema}`. `tests/known_photo.py` and `tests/known_document.py` (on `pythonpath`) build the known-answer photo (planted EXIF) and documents (witness PDF via fpdf2, planted prompt injection) for e2e and smoke. Linked pages come from `web_pages` (`python -m http.server` over `tests/e2e/pages/`). The api gets a dead `HTTP(S)_PROXY` with `NO_PROXY=127.0.0.1,localhost`, so tests can never reach the internet; `SEARX_URL` points at a dead port. Web research tests use `recorded_api` / `recorded_web`: the api runs under a vcrpy cassette (`tests/e2e/recorded_api.py`) that replays real responses for the web and SearXNG while local services pass through; an unrecorded request fails. Traces go to `tests/e2e/fake_langsmith.py` (records `/runs/multipart`) only when a test passes `LANGSMITH_*` to `start_api(langsmith=…)`; `start_api` drops every inherited `LANGSMITH_*` variable, so tests never trace with your real key.
- Browser: `tests/e2e/test_web_chat.py`, `test_web_evidence.py` with pytest-playwright (OpenStreetMap tiles are blocked with `page.route`); the `web` fixture runs `next build` once per session and `next start` per test against the real api. Wait for `networkidle` before typing (chat must discover the agent first).
- Deployed: `tests/smoke/` (HTTP + browser), run via `just smoke <api-url> <web-url>`; not part of `just test`.

## Blockers
- none (DB backups deliberately deferred to S23 by the owner: hobby-level project for now)

## Testability debt
- Alembic autogenerate ignores tables not in our models (`include_name` in `migrations/env.py`), else it proposes dropping LangGraph's checkpoint tables. Review every autogenerated migration anyway.
- LangGraph's checkpoint tables are created by the library (`PostgresSaver.setup()` inside an Alembic migration); after upgrading `langgraph-checkpoint-postgres`, add a migration that calls `setup()` again, or new library columns won't exist.
- `Settings()` is built at import time in `main.py`. Fine while e2e boots a real process with env vars; revisit if in-process tests become necessary.
