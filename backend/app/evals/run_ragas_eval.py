"""RAGAS metrics over the frozen synthetic testset (Task 5 harness, RAG half).

Per testset row: retrieve top-k over the shared collection (unfiltered — the
synthetic questions aren't doc_type-tagged), answer with the production
drafting model (the metric must reflect the real generator), then score with
RAGAS: faithfulness, answer relevancy, context precision, context recall.

Grader = ``openai/gpt-4.1-mini`` by default (user-approved split 2026-07-15:
mechanical metric grading goes to the fast OpenAI-family model; the deep
scenario judge stays ``settings.judge_model``).

NOTE (2026-07-15): never run — depends on the frozen SDG testset, which was
retired before generation (see generate_testset.py for the difficulties and
rationale). Task 5's shipped harness is run_retrieval_eval.py (hit@k/MRR over
the hand-anchored golden set) + run_scenario_eval.py (gpt-5.1 judge). Kept
runnable for the capstone.

Live-API script (needs the evals dependency group):

    cd backend && uv run --group evals python -m app.evals.run_ragas_eval \
        [--limit 3] [--k 4] [--json out.json]
"""

from __future__ import annotations

import argparse
import json

from app.config import settings
from app.evals.dataset import load_ragas_testset

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

_ANSWER_SYSTEM = (
    "You are the retrieval-grounded answerer for a renovation-estimation "
    "assistant. Answer the question using ONLY the provided context chunks. "
    "Cite section numbers/project codes where relevant. If the context does "
    "not contain the answer, say so plainly."
)


def _answer(llm, question: str, contexts: list[str]) -> str:
    from langchain_core.messages import HumanMessage, SystemMessage

    blocks = "\n\n---\n\n".join(contexts)
    msg = f"CONTEXT:\n\n{blocks}\n\nQUESTION: {question}"
    return llm.invoke([SystemMessage(content=_ANSWER_SYSTEM),
                       HumanMessage(content=msg)]).content


def run(retriever_name: str = "dense", k: int = 4, limit: int | None = None,
        grader_model: str = "openai/gpt-4.1-mini") -> dict:
    from openai import AsyncOpenAI
    from langchain_openai import OpenAIEmbeddings
    from ragas import EvaluationDataset, SingleTurnSample, evaluate
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from ragas.llms import llm_factory
    from ragas.metrics import (
        AnswerRelevancy,
        Faithfulness,
        LLMContextPrecisionWithReference,
        LLMContextRecall,
    )

    from app.agent.llm import drafting_model
    from app.evals.run_retrieval_eval import make_retriever

    cases = load_ragas_testset()
    if limit:
        cases = cases[:limit]

    retriever = make_retriever(retriever_name)
    answerer = drafting_model()

    samples = []
    for case in cases:
        chunks = retriever.search(case.user_input, k=k)
        contexts = [c.text for c in chunks]
        samples.append(SingleTurnSample(
            user_input=case.user_input,
            retrieved_contexts=contexts,
            response=_answer(answerer, case.user_input, contexts),
            reference=case.reference,
            reference_contexts=case.reference_contexts,
        ))

    grader = llm_factory(
        grader_model,
        client=AsyncOpenAI(api_key=settings.openrouter_api_key,
                           base_url=OPENROUTER_BASE_URL,
                           timeout=120.0, max_retries=2),
    )
    embeddings = LangchainEmbeddingsWrapper(OpenAIEmbeddings(
        model=settings.embedding_model, api_key=settings.openai_api_key))

    result = evaluate(
        dataset=EvaluationDataset(samples=samples),
        metrics=[Faithfulness(), AnswerRelevancy(),
                 LLMContextPrecisionWithReference(), LLMContextRecall()],
        llm=grader,
        embeddings=embeddings,
    )

    df = result.to_pandas()
    metric_cols = [c for c in df.columns
                   if c not in ("user_input", "retrieved_contexts", "response",
                                "reference", "reference_contexts")]
    return {
        "retriever": retriever_name,
        "k": k,
        "n": len(df),
        "grader_model": grader_model,
        "answer_model": settings.drafting_model,
        "means": {c: round(float(df[c].mean()), 3) for c in metric_cols},
        "rows": [
            {"user_input": r["user_input"],
             **{c: (None if r[c] != r[c] else round(float(r[c]), 3)) for c in metric_cols}}
            for _, r in df.iterrows()
        ],
    }


def render_markdown(report: dict) -> str:
    lines = [
        f"### RAGAS eval — `{report['retriever']}` retriever, k={report['k']}, "
        f"n={report['n']} (answers: {report['answer_model']}, "
        f"grader: {report['grader_model']})",
        "",
        "| metric | mean |",
        "|---|---|",
    ]
    lines += [f"| {m} | {v} |" for m, v in report["means"].items()]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--retriever", default="dense")
    parser.add_argument("--k", type=int, default=4)
    parser.add_argument("--limit", type=int, help="score only the first N rows (smoke run)")
    parser.add_argument("--grader-model", default="openai/gpt-4.1-mini")
    parser.add_argument("--json", help="also write the full report to this path")
    args = parser.parse_args()

    report = run(retriever_name=args.retriever, k=args.k, limit=args.limit,
                 grader_model=args.grader_model)
    print(render_markdown(report))
    if args.json:
        with open(args.json, "w") as f:
            json.dump(report, f, indent=2)
        print(f"\n(full report -> {args.json})")


if __name__ == "__main__":
    main()
