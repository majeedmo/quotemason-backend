"""One-time RAGAS synthetic testset generation (Task 5 dataset, component A).

Generates a synthetic QA testset over the real corpus using RAGAS's testset
generator, then freezes it to ``app/evals/data/ragas_testset.jsonl`` (committed).
Freezing matters: every eval run — including the Task 6 dense-vs-hybrid
comparison — must score the identical question set.

Generator LLM defaults to a fast OpenAI-family model (see ``--model``):
questions are never authored by the Anthropic models that answer them, keeping
the cross-family separation. Embeddings are OpenAI-direct, matching the
ingestion pairing.

STATUS (2026-07-15): deliberately not run — SDG is retired for this build.
Generation proved slow and fragile in practice: gpt-5.1 as generator was ~10x
slower and stall-prone over the hundreds of structured-output calls SDG makes;
the plain LangChain wrapper had models answering in prose and crashing ragas's
JSON parser mid-transform (fixed with the instructor tool-calling wrapper
below); a handful of hung requests froze the pipeline at 98/103 until the
explicit client timeout was added; and because output is only written at the
very end, an overnight machine restart lost an entire run. Since the rubric
accepts an assembled dataset ("either by generating synthetic data or by
assembling an existing dataset"), the frozen Task 5 dataset is the
hand-anchored golden set in ``app/evals/data/`` instead — exact clause-level
ground truth the synthetic path couldn't match. Kept for reference/capstone.

Live-API script — requires OPENROUTER_API_KEY and OPENAI_API_KEY. Run once:

    cd backend && uv run --group evals python -m app.evals.generate_testset

Refuses to overwrite an existing testset without ``--force``.
"""

from __future__ import annotations

import argparse
import json
import random

from app.config import settings
from app.evals.dataset import RAGAS_TESTSET_PATH
from app.ingestion.loaders import load_all

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# Defined-term compendia are huge and only generate definition trivia; synthetic
# quotes exist for eval Q4 and must not seed questions of their own.
_SKIP_SUBSTRINGS = ("defined-terms", "part3-definitions")


def _source_documents(cap: int, seed: int):
    from langchain_core.documents import Document

    docs = [
        d for d in load_all()
        if not any(s in d.source_file for s in _SKIP_SUBSTRINGS)
        and not d.metadata.get("synthetic")
        # CSVs and stubs break RAGAS's headline-based graph transforms
        and not d.metadata.get("tabular")
        and len(d.text) > 1500
    ]
    by_type: dict[str, list] = {}
    for d in docs:
        by_type.setdefault(d.metadata["doc_type"], []).append(d)

    # Guidelines are few — keep all; sample the rest proportionally up to cap.
    rng = random.Random(seed)
    chosen = list(by_type.pop("builder_guideline", []))
    remaining = cap - len(chosen)
    pool = [d for group in by_type.values() for d in group]
    weights = {t: len(g) for t, g in by_type.items()}
    total = sum(weights.values())
    for doc_type, group in by_type.items():
        n = min(len(group), max(2, round(remaining * weights[doc_type] / total)))
        chosen.extend(rng.sample(group, n))
    del pool

    return [
        Document(
            page_content=d.text,
            metadata={
                "source_file": d.source_file,
                "doc_type": d.metadata.get("doc_type", ""),
                "section_number": str(d.metadata.get("section_number", "")),
                "title": str(d.metadata.get("title", "")),
            },
        )
        for d in chosen
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--model", default="openai/gpt-4.1-mini",
        help="generator model (OpenRouter slug). Default is a fast non-reasoning "
             "OpenAI model: SDG makes hundreds of structured-output calls, and a "
             "reasoning model (gpt-5.1) proved 10x slower and stall-prone here. "
             "Still OpenAI-family — questions are never authored by the Anthropic "
             "models that answer them; the 5b judge remains settings.judge_model.")
    parser.add_argument("--size", type=int, default=36, help="testset size (QA rows)")
    parser.add_argument("--cap-docs", type=int, default=24, help="max source documents fed to SDG")
    parser.add_argument("--seed", type=int, default=42, help="document-sampling seed")
    parser.add_argument("--force", action="store_true", help="overwrite an existing frozen testset")
    args = parser.parse_args()

    if RAGAS_TESTSET_PATH.exists() and not args.force:
        raise SystemExit(
            f"{RAGAS_TESTSET_PATH} already exists — the testset is frozen. "
            "Re-run with --force only if you intend to invalidate all prior eval results."
        )
    if not (settings.openrouter_api_key and settings.openai_api_key):
        raise SystemExit("OPENROUTER_API_KEY and OPENAI_API_KEY must be set in backend/.env")

    from langchain_openai import OpenAIEmbeddings
    from openai import AsyncOpenAI
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from ragas.llms import llm_factory
    from ragas.testset import TestsetGenerator

    # Instructor-based wrapper (tool-calling structured output): the plain
    # LangChain wrapper had the model answering in prose and failing ragas's
    # JSON parser mid-transform. The client timeout is load-bearing — without
    # it, a handful of hung requests froze the whole pipeline at 98/103.
    generator_llm = llm_factory(
        args.model,
        client=AsyncOpenAI(
            api_key=settings.openrouter_api_key,
            base_url=OPENROUTER_BASE_URL,
            timeout=120.0,
            max_retries=2,
        ),
        temperature=0.3,
    )
    embeddings = LangchainEmbeddingsWrapper(OpenAIEmbeddings(
        model=settings.embedding_model,
        api_key=settings.openai_api_key,
    ))

    documents = _source_documents(cap=args.cap_docs, seed=args.seed)
    counts: dict[str, int] = {}
    for d in documents:
        counts[d.metadata["doc_type"]] = counts.get(d.metadata["doc_type"], 0) + 1
    print(f"SDG over {len(documents)} source documents: {counts}")

    generator = TestsetGenerator(llm=generator_llm, embedding_model=embeddings)
    testset = generator.generate_with_langchain_docs(documents, testset_size=args.size)

    rows = testset.to_pandas().to_dict(orient="records")
    with RAGAS_TESTSET_PATH.open("w") as f:
        for row in rows:
            f.write(json.dumps({
                "user_input": row.get("user_input", ""),
                "reference_contexts": list(row.get("reference_contexts") or []),
                "reference": row.get("reference", ""),
                "synthesizer_name": row.get("synthesizer_name", ""),
            }) + "\n")
    print(f"Wrote {len(rows)} rows to {RAGAS_TESTSET_PATH}")


if __name__ == "__main__":
    main()
