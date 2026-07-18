# QuoteMason

**An agentic estimation assistant for residential renovation contractors.**

AI Engineering Certification Challenge (AI Maker Space v1.0). QuoteMason runs structured intake for residential basement-renovation requests, retrieves comparable past projects, builder guidelines, Ontario Building Code Part 9, and Cambridge (ON) zoning provisions, checks current material pricing via Tavily, and drafts a fully cited quote — which a human estimator always reviews before anything reaches a client.

The full written submission is in [`docs/submission.md`](docs/submission.md), with a task→code traceability map in [`docs/deliverables.md`](docs/deliverables.md).

**Demo video (≤10 min):** <https://www.loom.com/share/c5fc1f064dd24c2290264a0e48bc0915>

## Live deployment

- **Frontend (Vercel):** <https://quotemason-frontend.vercel.app> — landing page, `/estimate` intake chat, `/estimator` review console
- **Backend API (Render):** <https://quotemason-api.onrender.com> — FastAPI; interactive docs at [`/docs`](https://quotemason-api.onrender.com/docs)

Render's free tier spins the API down when idle — the first request after a quiet period can take ~a minute to cold-start.

## Layout

```
corpus/                     RAG corpus (all committed data is PII-redacted)
  OBC/part9_phase1/         2024 OBC Part 9 — 15 extracted, metadata-tagged sections
  cambridge-zoning-bylaw/   By-law 26-007 (Phase 1, residential zones)
  quotes-redacted/          21 real past-project quotes, P01-P21 (redacted)
  quotes-synthetic/         2 synthetic tier-pairs for eval Q4 (S01, S02)
  guidelines/               Builder guidelines draft + labor/allowance tables
quotes/                     ORIGINALS — gitignored, never committed (PII)
scripts/                    Data prep: OBC extraction, quote redaction, bylaw refresh stub
backend/                    Python (uv) — ingestion, retrieval, LangGraph agent,
                            FastAPI (intake chat + estimator review gate)
docs/                       Written submission + eval Q4 ground truth
```

The Next.js frontend (landing page for the fictional "Maplewood Renovations"
brand, `/estimate` intake chat, `/estimator` review console) lives in a
**separate repo**: `quotemason-frontend` (deployed to Vercel at
<https://quotemason-frontend.vercel.app>).

The source PDFs (2024 OBC compendium, Cambridge By-law 26-007) are **not
committed** — they're large, re-downloadable from official Ontario/Cambridge
sources, and Crown-copyright redistribution is unclear. The extracted markdown
in `corpus/` is the actual RAG input; the extraction scripts in `scripts/`
need the PDFs locally to re-run.

## Backend quickstart

```bash
cd backend
cp .env.example .env        # OPENAI_API_KEY (ingestion/retrieval) + OPENROUTER_API_KEY (agent) at minimum
uv sync

# validate the corpus -> chunk pipeline (no API calls):
uv run python -m app.ingestion.ingest --dry-run

# embed + upsert into Qdrant (local on-disk store unless QDRANT_URL is set):
uv run python -m app.ingestion.ingest

# chat with the agent locally (REPL):
uv run python -m app.agent.cli

# run the API — customer intake chat + estimator review gate:
uv run uvicorn app.api.main:app --reload

# tests (no network, no keys needed):
uv run pytest
```

Ingestion is structure-aware (OBC articles, quote work-categories, guideline sections — never fixed token windows), stamps every chunk with `{jurisdiction, doc_type, section_number, source_version, …}`, prefixes the citation into the chunk text, and upserts with deterministic IDs into one shared collection filtered at query time.

The agent graph runs `intake → (ask | hard-route | codes → takeoff → price-fill → draft)` — a staged drafting pipeline (applicable codes via the shared regulatory tool, structured material takeoff, deterministic sheet-first price resolution with web fallback, then the cited draft) — checkpointed in Upstash Redis (conversation memory). Completed drafts persist to Neon Postgres as versioned `quote_drafts` rows for the estimator review gate: `GET /quotes` (queue) · `POST /quotes/{id}/edit` (logged to LangSmith as labeled eval data) · `POST /quotes/{id}/revise` (resumes the same thread, new version supersedes) · `POST /quotes/{id}/approve` (returns a `mailto:` stand-in — the agent never sends anything itself).

## Frontend quickstart

In the `quotemason-frontend` repo:

```bash
npm install
cp .env.local.example .env.local   # NEXT_PUBLIC_API_BASE (defaults to localhost:8000)
npm run dev                        # with the backend API running
```

Three responsive routes (phone + laptop requirement): `/` fictional-contractor landing page → `/estimate` customer intake chat (per-tab thread resumes its Upstash checkpoint) → `/estimator` QuoteMason review console (queue, edit, request-changes revision, approve with copy/mailto stand-in send). Details: the frontend repo's README.

## Data-handling policies

- Client PII is redacted before any processing (`scripts/redact_quotes.py`); house numbers are anonymized, street + city kept for zoning context.
- Partner businesses are referred to as Company A / B / C only.
