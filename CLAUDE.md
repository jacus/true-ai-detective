# CLAUDE.md

True AI Detective: a private AI detective that investigates cases over time, built story by story as a
learning and reference project for LangChain, LangGraph, Pydantic and model observability.

Read first: **`PLAN.md`** (decisions, board, current state: the source of truth), `.shift-zero/PORT.md`
(every command, proven), `README.md` (the vision). The web app has its own rules: `web/AGENTS.md`.

## How we work

- **One story per session.** Present the plan for a story and wait for the owner's go-ahead, implement it,
  stop, then walk the owner through the code at a high level (the owner is here to learn). Short sessions.
- **No custom wrappers (must-have).** Use LangChain, LangGraph, OpenAI, CopilotKit, MCP… directly, as their
  docs show. Defining LangChain tools with `@tool` is the library's way; a facade over a library is not.
  Tests use the libraries' own fakes, fake HTTP servers or recorded HTTP.
- **`PLAN.md` is not a journal.** Edit it in place to match the latest decisions and state; never append
  history (git has that). Settle a story's open decision in its row and decisions table, then delete it.
- **Vertical slices, observable end to end** (browser or HTTP), with at least one e2e check each.
- **Very cheap models only** (`gpt-5.4-nano`, `text-embedding-3-small`, JEV). Tests never make paid calls
  and never reach the internet.
- **Ask before outward-facing actions** (publishing, deleting, filing issues upstream).
- **Local until the owner says otherwise.** Build and commit locally (small, organised commits on `main`);
  push, open a PR, merge or deploy only on the owner's command.

## Shift Zero: the gate is the CI

There is no external CI. [Shift Zero](https://github.com/jacus/shift-zero) (a Claude Code plugin, enabled in
`.claude/settings.json`) runs CI inside the session: before replying "done", prove the change through the real
interface, then write `.shift-zero/evidence.json` and pass `just szero gate`, which replays the evidence
against the working tree and blocks the reply if it is stale or failing.

- Write the e2e check first when practical; run the scoped checks from `PORT.md`, then the full suite.
- **Revert check:** break each new protection on purpose and show a test catches it.
- **Never edit, skip or loosen an existing test to get green** without the owner's agreement. When behaviour
  changes on purpose, say exactly which test changed and why; record weakened tests in `tests_weakened`.
- Reply with the verdict line first: `SHIFT ZERO: VERIFIED | L0 — …`.
- When the owner ships (PR merged, Railway deploys `main`), run `just smoke` against Railway (addresses from `.env`).

## Commands

`just setup` once, then `just dev` (Postgres, S3, api on :8000) and `just web` (:3000); `just test`,
`just check`, `just migration "<msg>"`, `just graphs`, `just record-research`, `just infra-plan`. Copy
`.env.example` to `.env` (explained there). Full list with what each proves: `.shift-zero/PORT.md`.

## Code map (`src/detective/`)

- `agent.py`: the detective graph (recall → JEV decides → research/ask/consult/conclude → board). Run it to
  draw `docs/graphs/detective.png`.
- `research_agent.py`: the research agent (`create_agent` + middleware: plan, budget, filing findings).
  `research.py`: its tools (`@tool`, pictures too) and filing web findings as evidence. `decision.py`: JEV via
  AI Gateway. `memory.py`: case memory (pgvector, hybrid search). `board.py`: the case board.
- `evidence.py` + `evidence_analysis.py` + `photo_analysis.py`: the locker and its analysis graphs.
- `auth.py`, `cases.py`, `db.py` (RLS per request), `models.py` (+ `migrations/`), `cli.py`, `main.py`.
- `web/`: Next.js 16 + CopilotKit v2. `tests/e2e/`: real processes against fakes. `tests/smoke/`: deployed.

## Lessons that cost us time

- Every structured-output or tool-calling model call uses `disable_streaming=True`: anything streamed
  inside the detective's run reaches the chat as text.
- Web tools fail with `ToolException` (`handle_tool_error` on). An exception escaping a tool reaches
  ag-ui-langgraph's event serialiser, which walks objects; a big one took 16 GB.
- Anything run inside a node is a subagent to ag-ui-langgraph: the browser gets the detective's state only,
  replaced at each inner step. Live progress from inside goes as a custom event (`adispatch_custom_event`),
  kept in its own React state.
- `create_agent` with a structured report (OpenAI structured output) ignores `request.tool_choice` from
  middleware: force a tool through `request.override(model_settings={"tool_choice": name})`.
- From Railway's shared addresses SearXNG's default general engines are rate-limited or CAPTCHA'd, and
  `unresponsive_engines` says so; the news category still answers. Compose keeps SearXNG's settings in an
  anonymous volume: `--renew-anon-volumes` to pick up `searxng/settings.yml` changes (`just record-research` does).
- Row-level security for library connections: `db.acting_user` ContextVar + the engine `begin` hook.
- langchain-postgres: a fresh `HybridSearchConfig` per search (0.0.19 reuses the first query); no HNSW
  index for per-case filtered search.
- `@traceable` functions need `# ty: ignore[invalid-argument-type]` at call sites (ParamSpec).
- Write `evidence.json` with Python, never an unquoted heredoc (backticks execute).
- Run Railway IaC through `just` or plain `bash -c 'railway …'`: the SDK finds the CLI via `$_`.
- Migration tests name their revision, never `downgrade -1`; restore `head` in a `finally`.
- Secrets live on Railway only (`preserve()` in `.railway/railway.ts`, set with
  `railway variable set NAME --stdin --service <svc>`); never echo them into commands, logs or git.
