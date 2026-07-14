# CLAUDE.md

## Project
**QuoteMason** — an agentic estimation assistant for residential renovation contractors (name decided 2026-07-12; always pair the name with this descriptive subtitle in deliverables). AI Engineering Certification Challenge (AI Maker Space). Due 7pm ET, July 16, 2026.

Full context, rationale, and open items: @docs/project-brief.md

## Non-negotiable scope boundaries
Do not build or suggest these, even if they look like natural improvements — they are explicitly deferred to the capstone:
- Blueprint/CAD vision parsing — intake uses text description plus stated sqft/room-count/finish-level only
- Live scraping of vendor websites (Home Depot etc.) — pricing goes through Tavily agentic search only
- Multi-municipality or multi-province zoning coverage — Cambridge, ON zoning bylaw only, plus Ontario Building Code Part 9 (province-wide, ingest once)
- Company B's custom-home/ADU use case
- A fully automated bylaw-refresh pipeline — only ship `effective_date`/`source_version` metadata fields plus a manual `refresh_bylaw.py` stub

## Hard requirements
- Must have a memory component (Upstash Redis) — satisfied by Redis backing the LangGraph checkpointer for the intake conversation thread; not a separate cross-session recall feature
- Must run on phone and laptop in a browser (Next.js, responsive)
- Must include both a personal-data RAG component and a public agentic search tool (Tavily) — both required, not either/or
- Agent drafts, licensed estimator always reviews and approves before anything reaches a client. This is a hard product principle — do not build an auto-send path, even for a demo. Single estimator persona: the same person reviews the draft and decides to send (stand-in send action — `mailto:`/copy, not a real email service — this build) or edit.
- **PII redaction (standing policy, 2026-07-12)**: every quote — current and future — must be redacted **before any processing** (RAG ingestion, analysis, doc drafting, demo material). Pipeline: originals go in gitignored `quotes/`, register the file + client name tokens in `quotes/redaction-map.json` (local-only), run `scripts/redact_quotes.py`, work only with `corpus/quotes-redacted/`. Address rule: anonymize the **house number only** (`[NO] Rochelle Way, Mississauga`) — **street name and city are kept** because street + city is the zoning-lookup signal. Full redaction list: client names/emails/phones, house numbers, postal codes, permit/estimate numbers, contractor identity/address/licence/HST/WSIB numbers.
- **Business naming (standing policy, 2026-07-12)**: no disclosure permission for partner business names — refer to them only as **Company A, Company B, Company C, …** consistently in every committed file, prompt, code comment, and deliverable (Company A = the renovation contractor whose quotes seed the corpus; Company B = the custom-home/ADU builder, capstone scope). The redaction pipeline enforces this for quote text (business name → "Company A"); enforce it manually everywhere else, including the demo video and submission doc.

## Primary user
The estimator at a small residential renovation contractor (Company A) — not the homeowner. Full problem statement: @docs/project-brief.md

## Tech stack (locked)
LangGraph · OpenRouter · Tavily · Upstash Redis · Neon Postgres · Qdrant (vector DB, per Session 1 course precedent) · RAGAS · LangSmith · Next.js · Vercel (frontend) + Render (backend, chosen over Vercel/Docker which didn't work)

Models (decided 2026-07-12, via OpenRouter): drafting = `anthropic/claude-sonnet-5` · intake = `anthropic/claude-haiku-4.5` · eval judge = `openai/gpt-5.1` (cross-family by design — don't judge Anthropic drafts with an Anthropic judge) · embeddings = OpenAI-direct `text-embedding-3-small` (not via OpenRouter). Fallbacks + rationale: project-brief.md Task 2 "Model selection" table.

## RAG architecture
- Structure-aware chunking by section/clause — not fixed token size
- Every chunk metadata: `jurisdiction`, `doc_type`, `section_number`, `effective_date`/`source_version`
- One shared vector store/collection, filtered by jurisdiction + doc_type at query time — never a separate index per municipality
- Advanced retriever target for Task 6: hybrid dense + keyword (BM25) search

## Data sources
1. Past renovation projects — RAG ingests `corpus/quotes-redacted/` (21 PII-redacted markdown files, P01-P21, frontmatter `{project_code, city, street, package_tier, scope, revised}`, produced by `scripts/redact_quotes.py` per the PII policy above), **never** the original `quotes/` folder (gitignored — contains client PII and the undisclosed business name; keep it local-only). Real package tiers are ESSENTIAL/SUPERIOR/SUPREME (use this vocabulary, not generic "finish level"). Eval Q4's tier pairs exist as synthetic quotes in `corpus/quotes-synthetic/` (S01↔P19, S02↔P20; `synthetic: true` + `paired_with` in frontmatter) — ingest them alongside the redacted corpus, but never put the answer key (`docs/eval-q4-synthetic-pairs.md`) into the corpus. Do not model the schema on the Belfor insurance-restoration reference doc, it's the wrong shape
2. Builder guideline documents — stand-in **drafted** at `corpus/guidelines/builder-guidelines-DRAFT-v0.md` + `labor-rates-DRAFT-v0.csv` + `material-allowances-DRAFT-v0.csv`; values are tagged [GROUNDED] (from real quotes) vs [PLACEHOLDER] (pending business-owner review, next phase) — the agent must surface placeholder-derived line items as "rate unverified"; guideline doc §6 defines the two-tier manual-intervention triggers (HARD ROUTE = stop drafting, route to estimator; FLAG = draft with estimator-acknowledgment block) — implement routing from that section's keyword lists, they are the source of truth, not code constants; capstone gets the owner-authored version
3. Ontario Building Code Part 9 — Phase 1 sections **already extracted** (user-approved list, 2026-07-12) as 15 metadata-tagged files in `corpus/OBC/part9_phase1/`, produced by `scripts/extract_obc_part9_phase1.py` from `corpus/OBC/301880.pdf`; build the RAG pipeline on those files, don't re-parse the compendium. Phase 2 (stretch, only once the pipeline runs end-to-end) = the full Part 9 range (pdf pages ~715-1120); never the whole compendium or `corpus/OBC/301881.pdf`. Numbering gotchas (2024 code): CO alarms = 9.32.3.9/9.32.3.9A (not 9.33.4), bedroom egress windows = 9.9.10, suite-conversion trigger = 9.41
4. Cambridge, ON zoning bylaw — sourced as `corpus/cambridge-zoning-bylaw/phase1-zoning-bylaw-26-007-draft-dec2025.pdf` (By-law 26-007, enacted 2026-02-03, replaces old 150-85 for residential zones — the correct corpus for this build); ingest it with `source_version: "26-007 draft (Dec 2025)"`; `council-report-25-034-PG.pdf` in the same folder is a staff report — never ingest it. Details: project-brief.md Task 3 item 4

## Build layout
This project is its own git repo at `~/work/code/quotemason-backend` (split out of the course repo 2026-07-14; the old path `~/work/code/aiec01/MM_Certification_Challenge` is a compatibility symlink). Source PDFs under `corpus/` are gitignored (re-downloadable, heavy, Crown-copyright caution) — the extracted markdown is what's committed. Backend lives in `backend/` (uv project): `app/config.py` (pydantic-settings, reads `backend/.env`; documented in `backend/.env.example`), `app/ingestion/` (loaders → structure-aware chunkers per doc_type → Qdrant upsert; `--dry-run` validates without API calls), `app/retrieval/` (filtered dense search over the shared collection), `app/agent/` (LangGraph graph + guideline-driven trigger routing; memory = custom `UpstashRedisSaver` in `redis_checkpointer.py` — the official RedisSaver needs RediSearch, which Upstash doesn't support, so don't "upgrade" back to it), `app/quotes/` (Neon `quote_drafts` versioned review store), `app/api/` (FastAPI: `/chat` intake + estimator review-gate endpoints; on intake completion `/chat` pauses the graph via `interrupt_before=["retrieve"]`, replies immediately, and finishes drafting in a background task — the customer never sees the draft; `/approve` returns the `mailto:` stand-in, addressed to the captured contact email). Chunk IDs are deterministic — re-running ingestion is an idempotent upsert, which is what `scripts/refresh_bylaw.py` relies on. Frontend in the **separate `quotemason-frontend` repo** at `~/work/code/quotemason-frontend` (split out of `frontend/` 2026-07-14 for its own GitHub repo + Vercel deploy; Next 16 + Tailwind): `/` landing page for the **fictional brand "Maplewood Renovations"** (never a real business name), `/estimate` intake chat (contact gate first — email required, rides the routing packet; sessionStorage thread_id; attachments widget is a client-side stub, no vision parsing per scope boundary), `/estimator` review console (no auth by design; approve = copy-to-clipboard primary + mailto secondary); talks to the API via `NEXT_PUBLIC_API_BASE`, backend allows it via `CORS_ORIGINS`. Root README is the project readme; course instructions moved to `docs/challenge-instructions.md`. Current status + next steps: project-brief.md "Build status".

## Open action items and product-flow decisions
See "Open action items" and "Product flow clarifications" in @docs/project-brief.md — kept there, not duplicated here.

## When in doubt
Check @docs/project-brief.md before making a scope or architecture call that isn't covered above.

`docs/product-analysis.md` is real-product (post-challenge) analysis — competitive landscape, moat, roadmap. Do not pull items from it into the challenge build unless explicitly asked; it exists so those ideas stay out of scope now without getting lost.
