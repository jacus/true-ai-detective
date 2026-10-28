# True AI Detective — Plan & Board

Single source of truth for the current plan and state. Edited in place to match the latest decisions; history lives in git.

## How we work

- **One story per session.** Claude implements one story, stops, and walks you through the code at a high level.
- **Vertical slices.** Every story is visible end to end (browser or API) and ships with at least one e2e check (Shift Zero gate, see `.shift-zero/PORT.md`).
- **Use libraries directly, as documented.** No custom wrappers or facades over LangChain, LangGraph, OpenAI, CopilotKit, MCP, etc. Tests use the libraries' own fakes (e.g. `GenericFakeChatModel`) or recorded HTTP.
- **Isolation is tested in the story that adds the data.**
- **Deploy early, deploy often.** Railway from S2 onward; no GitHub Actions (D14).
- **New tools are a feature.** Each story lists what you'll learn.

Status legend: ⬜ todo · 🟨 in progress · ✅ done · ⏸️ blocked

## Decisions

| # | Topic | Decision |
|---|---|---|
| D1 | Case realism | Real OSINT, plus your own known-answer cases for tests and evals |
| D2 | OSINT boundary | Anything reachable. The detective may ask the client for extra access (API keys, logins), stored encrypted and scoped per client (S14) |
| D3 | LLM provider | OpenAI via LangChain `init_chat_model`. Key verified; available: `gpt-5.4-mini`/`gpt-5.4-nano` (chat + vision), `text-embedding-3-small/large`, `gpt-image-2` family |
| D4 | Model calls | Multimodal chat, separate vision extraction step, image generation, embeddings for RAG |
| D5 | Wrappers | **None.** Libraries used as documented |
| D6 | UI | React + CopilotKit + AG-UI, FastAPI agent backend |
| D7 | Auth | Owner-issued invite links → client sets password → session cookie; roles: owner, client |
| D8 | Isolation | App checks **and** Postgres row-level security (RLS) |
| D9 | Tools | LangChain harness tools first; untrusted-web tools move to an MCP server in S20 |
| D10 | Local infra | Docker Compose (`pgvector/pgvector:pg18`) for dev only; testcontainers for e2e |
| D11 | Prod infra | Railway project `true-ai-detective` (europe-west4), defined in **Railway IaC** `.railway/railway.ts` (`railway config plan/apply`). Postgres+pgvector is its own service (`pgvector/pgvector:pg18`, same image as local/tests) on a persistent volume, so it survives app and DB redeploys; no public TCP proxy, private network only. `api` service built by Railpack from GitHub `main`, `DATABASE_URL` references `pgvector.DATABASE_URL_PRIVATE`. Later: Railway Bucket (S3), `web` service |
| D12 | Tooling | Python 3.14, uv, ruff, ty, pytest, `just` as task runner |
| D14 | CI & deploy | **No external CI.** The in-session Shift Zero gate is the CI (every change goes through Claude, scope adjusted to the change). Railway auto-deploys `main` from GitHub; Railway's `/health` healthcheck blocks broken deploys; a smoke test against the public URL runs in-session after each deploy |
| D13 | Decision model | JEV (`typesafe-ai/jev`, Vercel AI Gateway) is a candidate for fast typed decisions (next-step routing, relevance scoring). Verified working via AI SDK `experimental_decide` |

### Open decisions (settled in their story)

- **S3:** how CopilotKit's runtime sits between Next.js and FastAPI, and how the session cookie flows (follow CopilotKit's documented LangGraph-Python setup)
- **DB backups:** Railway backup schedules need a Pro workspace (currently not Pro; PITR doesn't support the pgvector image). Options: upgrade to Pro, or scheduled `pg_dump` to the Railway Bucket once it exists (S7)
- **S7:** local S3-compatible storage (MinIO vs alternatives)
- **S12:** how the Python graph calls JEV. With the Gateway key it's only callable from the TypeScript AI SDK; from Python it needs TypeSafe's `typesafe-sdk` with a `TYPESAFE_API_KEY`. Alternative: route with an OpenAI model
- **S13:** search API provider (Tavily / Brave / Exa)
- **S18:** observability (self-hosted Langfuse on Railway vs LangSmith)
- **S19:** eval tooling

## Prerequisites (your side)

- [x] Docker in WSL
- [x] OpenAI API key (`.env`)
- [x] Vercel AI Gateway key + `JEV_MODEL` (`.env`)
- [ ] A few **known-answer cases** to feed the detective (needed by S8)

## Architecture

```text
Browser ── Next.js + CopilotKit (web service)
              │  AG-UI (SSE) + session cookie
              ▼
          FastAPI (api service) ── LangGraph investigation graph ── OpenAI (chat / vision / image / embeddings)
              │                          │                        └ JEV (typed decisions, TBD S12)
              │                          │ harness tools → (S20) MCP osint server
              ▼                          ▼
          Postgres + pgvector (RLS, checkpoints, audit)     S3 bucket (evidence files, per-case prefix)
```

Current state: FastAPI app with `GET /health` (DB round-trip + pgvector availability), deployed at https://<your-api>.up.railway.app (auto-deploy from `main`). Local: `just dev`, `just test`, `just check`; deployed: `just smoke <url>`; infra: `just infra-plan` / `railway config apply`. Config is real env vars only: `just` loads `.env` locally, Railway provides them in prod; `Settings` (pydantic-settings) types our own config.

---

## Board

### M0 — Foundations

| | Story | Done when (observable e2e) | You'll learn |
|---|---|---|---|
| ✅ | **S1 Walking skeleton (local).** As a dev, I can boot the API and DB with one command. | `just dev` → `GET /health` reports DB + pgvector; 503 when DB is down; e2e tests boot a real server against a throwaway pgvector container | uv, ruff, ty, just, docker compose, testcontainers, Shift Zero |
| ✅ | **S2 Deployed skeleton.** As the owner, I can open the API on a public Railway URL. | https://<your-api>.up.railway.app/health green (`just smoke <url>`); data survived api and DB redeploys; public DB proxy removed; Railway healthcheck guards deploys | Railway IaC (`railway config`), Railpack, volumes, variable references, private networking |
| ⬜ | **S3 Hello detective.** As a visitor, I chat with a one-node LangGraph detective in the browser and see the reply stream. | Playwright: type a message → streamed reply (fake model in tests, real OpenAI in dev); web service on Railway | LangGraph basics, `init_chat_model`, AG-UI, CopilotKit, Next.js, Playwright |

### M1 — Clients, cases & isolation

| | Story | Done when (observable e2e) | You'll learn |
|---|---|---|---|
| ⬜ | **S4 Invite & login.** As the owner I create a client and get an invite link; as a client I redeem it, set a password and log in. | Playwright full flow; expired, reused and forged invites rejected | DB layer + migrations (Alembic), argon2, secure session cookies |
| ⬜ | **S5 Cases with RLS.** As a client I create and open my cases; I never see another client's. | API e2e: client B gets 404 on A's case IDs; a DB-level test proves RLS blocks the query even with the app check bypassed | Postgres RLS, DB roles, `SET LOCAL` per request |
| ⬜ | **S6 Case conversations persist.** As a client, I leave and come back to a case and the conversation continues. | Restart api → conversation resumes; thread of case A unreadable from case B | LangGraph Postgres checkpointer, thread scoping |
| ⬜ | **S7 Evidence locker.** As a client I attach files, photos, URLs and map pins to a case. | Upload → listed in case → download via presigned URL; another client's URL/key fails | S3 API, Railway Buckets, presigned URLs, map widget |

### M2 — Evidence analysis

| | Story | Done when (observable e2e) | You'll learn |
|---|---|---|---|
| ⬜ | **S8 Photo analysis.** As a client, I upload a photo and the detective extracts facts (text, objects, place hints, EXIF/GPS), each linked to its source. | Known-answer photo → expected facts stored with source links | Vision model, structured output with Pydantic, EXIF parsing |
| ⬜ | **S9 Documents & links.** The detective reads PDFs, text and web pages I send. | Known-answer doc/URL → facts with citations; a planted prompt injection in evidence is treated as data | Document parsing, untrusted-content handling |
| ⬜ | **S10 Case board.** As a client I watch leads, hypotheses, a timeline and contradictions update live. | Playwright: new evidence → board updates without reload | CopilotKit shared state, LangGraph state design |
| ⬜ | **S11 Case memory search.** The detective recalls relevant facts from the whole case history. | Question answered with sourced facts; case B's embeddings never returned (test) | OpenAI embeddings, pgvector, hybrid full-text + vector search under RLS |

### M3 — The investigation loop

| | Story | Done when (observable e2e) | You'll learn |
|---|---|---|---|
| ⬜ | **S12 The loop.** The detective decides each turn: research, ask, consult or conclude, and asks me follow-up questions. | Scripted conversation walks every branch | LangGraph conditional edges, routing, JEV typed decisions |
| ⬜ | **S13 Web research.** The detective researches the web (search, fetch, Wayback, WHOIS/DNS) and stores results as evidence with provenance. | Recorded run → new evidence with source URLs + timestamps; audit events written | LangChain tools, tool calling, HTTP recording (VCR) |
| ⬜ | **S14 Client secrets vault.** The detective asks for extra access (e.g. an API key); I enter it in a secure form and it's used without the model ever seeing it. | Secret encrypted at rest, injected only at tool execution; test proves it is absent from LLM messages, checkpoints and logs; another client can't use it | Envelope encryption, secret references, tool-time injection |
| ⬜ | **S15 Consult & resume.** The detective pauses for me to verify an assumption; I answer days later and it continues. | Interrupt → restart → resume from checkpoint; HITL UI in browser | LangGraph `interrupt`, CopilotKit human-in-the-loop |
| ⬜ | **S16 Final report.** I get a report where every claim links to evidence. | Known-answer case → report with no unsourced claims (checked automatically) | Structured generation, citation checking |
| ⬜ | **S17 Composite sketch.** The detective generates a sketch or scene from a description, clearly marked as generated, not evidence. | Image stored as derived artifact with "generated" flag; shown in case | OpenAI image generation |

### M4 — Trust, quality & ops

| | Story | Done when (observable e2e) | You'll learn |
|---|---|---|---|
| ⬜ | **S18 Observability.** As the owner I see traces of every investigation step, with sensitive content redacted. | Trace visible for a run; a test shows evidence text and secrets are redacted | Langfuse/LangSmith, OpenTelemetry, self-hosting on Railway |
| ⬜ | **S19 Evals.** I measure how well the detective solves my known-answer cases. | Eval run produces a score table; regressions visible | Eval datasets, LLM-as-judge vs exact checks, JEV as a cheap grader |
| ⬜ | **S20 OSINT MCP server.** Untrusted web tools run in a separate MCP service with no DB access; I can also use it from Claude Code. | Detective uses tools via MCP; the MCP service has no DB credentials (checked); Claude Code can call it | MCP server, `langchain-mcp-adapters`, Railway private networking |
| ⬜ | **S21 Oversight.** As the owner I'm alerted to suspicious behaviour; deterministic controls block it even when oversight misses. | Injected attack → blocked by controls + alert raised; with oversight disabled → still blocked | Second agent as monitor, alerting, audit queries |
| ⬜ | **S22 Red-team suite.** One command proves isolation, injection resistance and recovery from failed or resumed workflows. | Suite green via one `just` recipe | Adversarial testing |
