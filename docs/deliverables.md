# Deliverables — Traceability Map

**QuoteMason** · AI Engineering Certification Challenge v1.0

This document maps every task deliverable from the challenge to (a) where it is answered in the written submission and (b) its exact location in the code/corpus. The full narrative lives in **[`submission.md`](submission.md)**; this is the grader's index.

**Live deployment:** frontend <https://quotemason-frontend.vercel.app> · backend API <https://quotemason-api.onrender.com> (docs at `/docs`)
**Repos:** backend `quotemason-backend` (this repo) · frontend `quotemason-frontend` (separate)
**Demo video:** see repo README / submission link
**Reference paths below are repo-relative; line numbers are anchors at time of writing and may drift — the named symbol is authoritative.**

---

## Task 1 — Defining Problem, Audience, and Scope

| # | Deliverable | Written answer | Code / artifact |
|---|---|---|---|
| 1 | 1-sentence problem (no solution implied) | `submission.md` §1.1 | — |
| 2 | Who has it / what they do / how today / why insufficient | `submission.md` §1.2 | grounding data: `docs/eval-q4-synthetic-pairs.md`, corpus `corpus/quotes-redacted/` |
| 3 | Current-state workflow diagram (steps, tools, pain points) | `submission.md` §1.3 (mermaid) | — |
| 4 | Eval questions / input-output pairs | `submission.md` §1.4 (7 questions) → become the Task 5 golden set | `backend/app/evals/data/agent_scenarios.json`, `backend/app/evals/data/retrieval_golden.jsonl` |

## Task 2 — Propose a Solution

| # | Deliverable | Written answer | Code / artifact |
|---|---|---|---|
| 1 | Solution in one sentence | `submission.md` §2.1 | — |
| 2 | Infrastructure diagram + one-sentence "why" per component (LLMs, orchestration, tools, embeddings, vector DB, monitoring, eval, UI, deployment, memory) | `submission.md` §2.2 (diagram + component table) | stack wired in `backend/app/config.py`, `backend/app/agent/llm.py`, `render.yaml` |
| 3 | Agent workflow diagram + 1–2 paragraph explanation (input, reasoning/decisions, RAG, tools, output, human review) | `submission.md` §2.3 (diagram + narrative) | realized in `backend/app/agent/graph.py` |
| R | Requirement: LLM gateway | §2.2 (OpenRouter) | `backend/app/agent/llm.py` |
| R | Requirement: memory component | §2.3 | `backend/app/agent/redis_checkpointer.py` (`UpstashRedisSaver`) |
| R | Requirement: runs in phone + laptop browser | §2.2 | frontend repo (responsive Next.js) |

## Task 3 — Dealing with the Data

| # | Deliverable | Written answer | Code / artifact |
|---|---|---|---|
| 1 | Default chunking strategy + why | `submission.md` §3.2 | `backend/app/ingestion/chunking.py` — per-`doc_type` splitters: `chunk_building_code` (L100), `chunk_quote` (L161), `chunk_guideline_md` (L186), `chunk_guideline_csv` (L204), `chunk_zoning_bylaw` (L229); dispatch `chunk_doc` (L276); deterministic IDs `_make_id` (L41); size guard `_size_guard` (L57) |
| 2 | Data sources + external API + roles + interaction | `submission.md` §3.1 | RAG corpus: `corpus/OBC/part9_phase1/`, `corpus/cambridge-zoning-bylaw/parts/`, `corpus/quotes-redacted/`, `corpus/quotes-synthetic/`, `corpus/guidelines/`. External API (Tavily): `backend/app/agent/nodes.py` → `pricing_node` (L154). PII redaction: `scripts/redact_quotes.py`. Ingestion: `backend/app/ingestion/ingest.py` (`--dry-run` → 730 chunks) |

## Task 4 — End-to-End Agentic RAG Prototype

| # | Deliverable | Written answer | Code / artifact |
|---|---|---|---|
| 1 | End-to-end prototype (intake → RAG → price → cited draft → human review) | `submission.md` §4.1 | Agent graph `backend/app/agent/graph.py` (nodes `intake`/`hard_route`/`retrieve`/`pricing`/`draft`, L42–56). Retrieval `backend/app/retrieval/retriever.py` (`CorpusRetriever` L67). Intake slots/state `backend/app/agent/state.py`. Guideline-driven routing `backend/app/agent/guidelines.py` (`section` L28). API `backend/app/api/main.py`: `/chat` (L98, `interrupt_before=["retrieve"]` L106), `/quotes` (L116), `/quotes/{id}/edit` (L129), `/quotes/{id}/approve` (L142), `/quotes/{id}/revise` (L162). Review store `backend/app/quotes/store.py` (Neon `quote_drafts`). Memory `backend/app/agent/redis_checkpointer.py`. Frontend: separate repo. Tests: `backend/tests/` (no-network) |
| 2 | Deployed to a public endpoint | `submission.md` §4.2 | `render.yaml` (backend → Render); frontend → Vercel. URLs above |

## Task 5 — Evals

| # | Deliverable | Written answer | Code / artifact |
|---|---|---|---|
| 1 | Test dataset (synthetic *or* assembled — assembled path chosen) | `submission.md` §5.1 | `backend/app/evals/data/retrieval_golden.jsonl` (24 hand-anchored retrieval cases), `backend/app/evals/data/agent_scenarios.json` (7 scenarios). Loaders/validators `backend/app/evals/dataset.py`. Retired SDG path (documented): `backend/app/evals/generate_testset.py`, `backend/app/evals/run_ragas_eval.py` |
| 2 | Evaluation harness (LLM-as-judge + retrieval metrics) | `submission.md` §5.2 | Retrieval metrics `backend/app/evals/run_retrieval_eval.py` (hit@k / MRR / filter violations). Agent scenarios `backend/app/evals/run_scenario_eval.py` (deterministic checks + judge). Cross-family judge `backend/app/evals/judge.py` (`openai/gpt-5.1`). Committed reports `backend/eval_results/`. Harness tests `backend/tests/test_evals_harness.py`, `backend/tests/test_evals_dataset.py` |
| 3 | Conclusions about pipeline performance | `submission.md` §5.3 (baseline tables) + §5.4 (4 conclusions) | Baselines: `backend/eval_results/retrieval_dense_baseline.json`, `backend/eval_results/scenario_eval_dense.json` |

## Task 6 — Improving Your Prototype

| # | Deliverable | Written answer | Code / artifact |
|---|---|---|---|
| 1 | Advanced retriever + why (1–2 sentences) | `submission.md` §6a | Hybrid dense+BM25+RRF: `backend/app/retrieval/hybrid.py` (`HybridRetriever` L53, `get_hybrid_retriever` L141). Config flip `backend/app/config.py` (`retriever`), dispatch `backend/app/retrieval/retriever.py` (`get_retriever` L123). Tests `backend/tests/test_retrieval_hybrid.py` |
| 2 | Performance vs. original RAG (results table) | `submission.md` §6b (dense vs. hybrid table) | `backend/eval_results/retrieval_dense_baseline.json` vs. `backend/eval_results/retrieval_hybrid.json` (same 24-case set, k=5) |
| 3 | One more improvement, with eval-harness evidence of a meaningful gain | `submission.md` §6c (priced-line traceability, before/after judge table) | Rule (source of truth): `corpus/guidelines/builder-guidelines-DRAFT-v0.md` quoting-rule #19 / §5.19 (L106). Prompt enforcement: `backend/app/agent/prompts.py` (`DRAFT_SYSTEM`, L75–83). Evidence: `backend/eval_results/scenario_q7_before.json` vs. `backend/eval_results/scenario_q7_after.json`. Supporting harness fix: `--only` cross-cutting pull in `backend/app/evals/run_scenario_eval.py` |

## Task 7 — Next Steps

| # | Deliverable | Written answer | Code / artifact |
|---|---|---|---|
| 1 | What to keep for Demo Day + what to change/improve, with reasoning | `submission.md` §7.1 (keep) + §7.2 (capstone plan: quote-accuracy eval priority, price tool/MCP, dedup guardrails, estimator auth) | — (forward-looking) |

---

## Final Submission checklist

| Required | Location |
|---|---|
| Public GitHub repo with all code | this repo (`quotemason-backend`) + `quotemason-frontend` |
| ≤10-min Loom demo (live demo + use case) | linked in repo `README.md` / submission |
| Written document addressing each deliverable | **[`submission.md`](submission.md)** (narrative) + this **`deliverables.md`** (traceability) |
| All relevant code | `backend/`, `corpus/`, `scripts/`, `render.yaml` |
