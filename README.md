# True AI Detective

A learning project: build a private AI detective that investigates cases over time, works with mixed evidence, remembers each client's case history, and produces evidence-backed findings.

The point is to learn production-minded AI engineering: Python, model APIs, LangGraph, retrieval, MCP, security boundaries, and observability. Keep the design as simple as possible; use frameworks when they solve a real problem.

## Example use cases

- **Find a lead:** A client shares a name, a few links, and a story. The detective asks follow-up questions, researches permitted sources, and records evidence and uncertainties.
- **Connect evidence:** A client provides documents, screenshots, photos, map locations, and URLs. The detective extracts useful details, builds a timeline, and identifies contradictions or gaps.
- **Resume an investigation:** The client returns days later with a new clue. The detective retrieves the right case memory, revisits hypotheses, and continues from where it left off.
- **Consult and conclude:** The detective shares interim findings, asks the client to verify assumptions, then creates a sourced report when there is enough evidence or the investigation reaches its limit.

The system should accept varied inputs; it is not an image-first product.

## How the investigation loop might work

```text
Client message / new evidence
          |
          v
  Load authorized case state
          |
          v
  Analyze evidence and update case
          |
          v
  Decide next step
    |       |        |          |
 research  ask      consult    conclude
    |       |        |          |
    +-------+--------+       final report
            |
      new info arrives
            |
       loop to analysis
```

LangGraph is a candidate to orchestrate this loop and support branching, checkpoints, client-approval pauses, and resuming later. The same flow can start as ordinary Python functions; use LangGraph when its workflow/state features are useful. LangChain is optional tooling for model and retrieval integrations, not a requirement.

## Memory and privacy

- Each client/case has separate persistent memory: conversation history, evidence, leads, hypotheses, and findings.
- Do **not** anonymize case data as a substitute for privacy. The system may need real details to investigate.
- Do prevent data from leaking across clients/cases. Enforce authorization in application/database/tool layers—not through prompts alone. Scope vector search, files, caches, background jobs, and memory retrieval to the authorized case.
- The oversight agent can flag suspicious behavior and alert the agency owner, but deterministic controls must block unauthorized actions even if the oversight agent misses them.
- Treat evidence and web content as untrusted input. Keep sensitive customer content out of logs/traces by default; configure model providers according to the intended privacy requirements.

## Initial technical direction

- **Python + FastAPI + Pydantic:** API and typed domain models.
- **PostgreSQL:** clients, cases, evidence metadata, conversations, permissions, and audit events.
- **Object storage:** uploaded files and derived artifacts.
- **Full-text search + pgvector:** search evidence, always filtered by access permissions.
- **Multimodal LLM API:** analyze different input formats; start with one capable model.
- **LangGraph:** stateful investigation workflow if needed.
- **MCP:** expose a small set of controlled investigation tools when there is a real integration use case.
- **Langfuse or LangSmith + OpenTelemetry:** model/workflow traces and end-to-end observability, with sensitive-data redaction.

## Build in small steps

1. Create cases and store evidence with strict case-level access checks.
2. Add evidence analysis and source-linked structured findings.
3. Implement the investigation loop and persistent memory, including client follow-ups.
4. Add the oversight checks, alerts, tool restrictions, and observability.
5. Test cross-client isolation, prompt injection in evidence, failed/resumed workflows, and source-backed reporting.

**Working rule:** prioritize a useful detective and demonstrable isolation. Don't add agents or frameworks just to tick boxes.