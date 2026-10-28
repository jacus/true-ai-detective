# True AI Detective

A private, evidence-driven AI investigation platform. The goal is to explore how to build a stateful AI system that can conduct an intake interview, organize heterogeneous evidence, investigate leads over time, consult the client, and produce a source-backed conclusion—without allowing information to leak between cases or clients.

This is an evolving project brief, not a claim that the security properties have already been implemented.

## Product vision

A client opens an investigation and describes what they want to establish. The detective asks clarifying questions, gathers and organizes evidence, investigates permitted leads, tracks hypotheses and uncertainty, and returns to the client when it needs clarification or approval. The client can return later with more information or new leads. The investigation can loop until it reaches a defined stopping condition and produces a final report.

The initial motivating scenario is an image-based investigation: a client provides a picture and asks for help understanding its context. The system may analyze non-identifying visual clues, text, objects, logos, landmarks, and other relevant context. It must not identify a real person from their face or claim an identity based on facial resemblance.

## Core workflow

1. **Intake:** create a case, clarify the client's objective, establish scope, and record relevant constraints.
2. **Evidence collection:** accept client statements and files in multiple formats, including images, scans, documents, URLs, and map/location references.
3. **Analysis and research:** extract useful information, search permitted sources, create leads, and connect evidence.
4. **Decision and iteration:** evaluate what is known, what remains uncertain, and whether to research further, ask the client, consult the client, or conclude.
5. **Client consultation:** present interim findings and questions; allow the client to provide more information and new leads later.
6. **Conclusion:** produce a report with sourced claims, reasoning, unresolved questions, and limitations. Preserve the case history and evidence provenance.

Steps 2–5 may loop and branch depending on findings. A case may pause and resume after hours or days. New evidence can change or invalidate earlier hypotheses. The system should not force every case through a rigid linear script.

## Evidence principles

Keep these concepts distinct:

- **Fact:** directly supported by an identifiable source.
- **Claim:** something stated by a client, witness, or external source.
- **Inference:** a conclusion drawn from one or more pieces of evidence.
- **Unknown:** a question that has not been resolved.

Every material conclusion should link to its supporting evidence. Record source, acquisition time, provenance, and relevant transformations. Preserve originals where appropriate and use integrity hashes for uploaded artifacts. Conflicting evidence should remain visible rather than being silently reconciled by the model.

Treat uploaded documents, web pages, images, and tool outputs as untrusted data. Instructions found inside evidence must never override system policy or grant permissions.

## Privacy and security are foundational

The detective handles sensitive customer information. Confidentiality is a system property, not a prompt instruction.

- Enforce tenant, case, and user authorization in deterministic application code and the data layer—not by asking an LLM to behave.
- Scope every evidence query, retrieval operation, file access, tool call, and background job to an authenticated principal and authorized case.
- Prevent cross-tenant and cross-case retrieval, including through vector search, caches, logs, traces, summaries, and agent memory.
- Give agents narrow, purpose-specific tools. Do not give the model unrestricted database credentials, shell access, or arbitrary network access.
- Validate authorization at execution time for every sensitive operation. Reject unauthorized requests before returning data to an agent.
- Minimize sensitive data sent to model providers and observability systems. Redact prompts, attachments, customer identifiers, and evidence content from traces by default.
- Document provider data handling, retention, processing region, and contractual constraints. An API key does not by itself make an external provider private.
- Keep secrets out of source control, prompts, case records, and logs.
- Support data retention and deletion policies, with explicit handling for derived artifacts, embeddings, caches, and backups.
- Maintain auditable records of actions, authorization decisions, tool invocations, and outcomes. Protect audit records against unauthorized modification.
- Add adversarial tests for cross-case access, prompt injection, data exfiltration, permission revocation, and sensitive-data leakage before treating the system as secure.

If a deployment requires that no customer information ever leave the operator's infrastructure, use an appropriately self-hosted model and controlled infrastructure. External model APIs do not meet that requirement by default.

## Detective and oversight

### Detective

The primary agent conducts intake, identifies questions, proposes hypotheses, requests or analyzes evidence, invokes approved research tools, tracks uncertainty, consults the client, and drafts findings.

### Oversight and agency-owner alerts

A separate oversight component reviews proposed or completed actions for policy violations and suspicious behavior, such as attempted cross-case access, unauthorized disclosure, or prohibited actions. It should produce actionable alerts with supporting audit events and minimize the sensitive information included in those alerts.

The oversight agent is a secondary detection layer, not the security boundary. It can miss violations or raise false alarms. Deterministic policy enforcement must block unauthorized actions regardless of the oversight agent's opinion. Define permitted investigation activities and escalation rules explicitly; do not rely on vague model judgments about legality.

## Initial technical direction

Start with the simplest architecture that makes the core workflow testable. Adopt frameworks when they solve a demonstrated problem, not merely to include them.

- **Python, FastAPI, Pydantic:** API, typed domain models, validation, and async I/O.
- **PostgreSQL:** tenants, users, cases, conversations, evidence metadata, hypotheses, permissions, workflow records, and audit events.
- **Object storage:** original files and derived artifacts, with tenant-aware authorization.
- **Full-text search and pgvector:** keyword and semantic evidence retrieval, always constrained by authorization.
- **Multimodal model API:** initial extraction and reasoning; use one capable model first rather than adding a separate vision model without evidence that it helps.
- **LangGraph:** a candidate for durable, stateful workflows with conditional loops, client-approval pauses, retries, and resume-after-interruption. It is not mandatory; compare against ordinary Python functions and persisted application state.
- **LangChain:** use selectively for useful model, prompt, structured-output, or retrieval integrations. It is not required just because LangGraph is used.
- **MCP:** expose narrowly scoped investigation tools to compatible external clients only when there is a concrete integration need. MCP is a protocol, not an authorization system.
- **Langfuse or LangSmith:** choose one initially for model/workflow tracing and evaluation. Redact sensitive data by default.
- **OpenTelemetry:** correlate API, database, background-job, and model operations through application-level traces.
- **Docker and CI:** reproducible local development, automated tests, and security regression checks.

Prefer explicit Python business rules for authorization, policy enforcement, evidence provenance, and other deterministic behavior. Use LLMs for interpretation and reasoning where appropriate, not as the final authority for access control.

## Suggested milestones

### Milestone 1 — Case and evidence foundation
- Define tenant, user, case, evidence, claim, hypothesis, and audit-event models.
- Implement authenticated case access and enforce tenant/case boundaries.
- Upload and store files with metadata and integrity hashes.
- Add tests proving users cannot read another case's data.

### Milestone 2 — Image/document intake
- Accept an image or document and extract structured, non-identifying clues.
- Keep model output separate from verified facts.
- Store source references and extraction provenance.
- Build a small evaluation dataset with expected extraction results.

### Milestone 3 — Stateful investigation loop
- Implement intake, evidence analysis, lead generation, client questions, consultation, and conclusion.
- Support new evidence and resume-after-pause.
- Start with ordinary Python; introduce LangGraph if persistence and branching make it worthwhile.
- Require sources for material factual claims.

### Milestone 4 — Tools and oversight
- Add a small set of allowlisted research tools.
- Enforce authorization and action policies in the tool execution layer.
- Add oversight checks, owner alerts, and auditable outcomes.
- Test malicious instructions embedded in evidence and attempted unauthorized access.

### Milestone 5 — Observability and hardening
- Add redacted tracing, structured logs, metrics, and evaluation.
- Test tenant isolation across database queries, vector search, caches, files, background jobs, and traces.
- Add retention/deletion behavior, dependency checks, and CI security tests.

## Definition of a credible early success

A user can open a case, provide an image and context, receive useful non-identifying observations, add more evidence later, and get a source-backed summary. A second user's data remains inaccessible even when inputs are malicious or the agent makes a mistake. Sensitive data is not casually copied into traces. The system can explain why it reached a conclusion and what remains uncertain.

## Working agreements

- Prefer working software and evidence-backed decisions over framework-driven complexity.
- Keep the first implementation small, but design security boundaries early.
- Do not claim the system is secure until the relevant controls and adversarial tests exist.
- Do not invent evidence, sources, identities, or certainty.
- Treat all model output as fallible and validate structured output.
- Measure quality with tests and evaluation cases, not demos alone.
- Keep architecture decisions explicit and revisit them as real requirements emerge.

## Out of scope for the first version

- Face recognition or identifying a person from facial appearance.
- Unrestricted autonomous browsing or arbitrary code execution.
- Automatic consequential actions without appropriate authorization and approval.
- Multiple specialist agents solely for appearance; add them only when responsibilities and measurable benefits justify them.
- Claiming legal compliance or production-grade security without a proper review.

## Name

**True AI Detective** — private, evidence-driven, and accountable investigation workflows.
