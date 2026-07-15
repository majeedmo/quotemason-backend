"""Retrieval eval over the hand-anchored golden set (Task 5 harness, retrieval half).

For every ``RetrievalCase`` this runs the case's doc_type helper against the live
collection and scores:

- **hit@k** — any ground-truth matcher matches any retrieved chunk
- **MRR** — reciprocal rank of the first ground-truth hit
- **violations** — retrieved chunks matching a ``forbidden`` matcher
  (e.g. synthetic quotes surfacing despite ``include_synthetic=False``)

The retriever is selected by name so Task 6 scores alternatives on the
identical dataset (``--retriever dense`` vs ``--retriever hybrid``).

Live-API script (Qdrant + embeddings):

    cd backend && uv run python -m app.evals.run_retrieval_eval [--retriever dense|hybrid] [--k 5] [--json out.json]
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, field

from app.evals.dataset import RetrievalCase, load_retrieval_golden


def _search(retriever, case: RetrievalCase, k: int):
    """Dispatch a case to its doc_type helper with the case's filters."""
    if case.doc_type == "building_code":
        return retriever.search_building_code(case.question, k=k)
    if case.doc_type == "zoning_bylaw":
        return retriever.search_zoning(
            case.question, k=k, jurisdiction=case.filters.get("jurisdiction", "cambridge"))
    if case.doc_type == "builder_guideline":
        return retriever.search_guidelines(case.question, k=k)
    if case.doc_type == "past_project_quote":
        return retriever.search_past_quotes(
            case.question, k=k,
            city=case.filters.get("city"),
            package_tier=case.filters.get("package_tier"),
            scope=case.filters.get("scope"),
            include_synthetic=case.filters.get("include_synthetic", True),
        )
    raise ValueError(f"unknown doc_type {case.doc_type!r}")


@dataclass
class CaseResult:
    id: str
    doc_type: str
    hit: bool
    rank: int | None            # 1-based rank of the first ground-truth hit
    violations: list[str] = field(default_factory=list)
    top_sections: list[str] = field(default_factory=list)

    @property
    def reciprocal_rank(self) -> float:
        return 1.0 / self.rank if self.rank else 0.0


def score_case(case: RetrievalCase, chunks) -> CaseResult:
    rank = None
    violations = []
    for i, chunk in enumerate(chunks, start=1):
        if rank is None and case.matches(chunk.metadata):
            rank = i
        if case.violates(chunk.metadata):
            violations.append(chunk.metadata.get("project_code")
                              or chunk.metadata.get("section_number", "?"))
    top = [str(c.metadata.get("project_code") or c.metadata.get("section_number", "?"))
           for c in chunks]
    return CaseResult(id=case.id, doc_type=case.doc_type, hit=rank is not None,
                      rank=rank, violations=violations, top_sections=top)


def make_retriever(name: str):
    """Explicit by name and independent of settings.retriever, so the dense
    baseline and the hybrid run are always compared apples-to-apples."""
    if name == "dense":
        from app.retrieval.retriever import CorpusRetriever
        return CorpusRetriever()
    if name == "hybrid":
        from app.retrieval.hybrid import get_hybrid_retriever
        return get_hybrid_retriever()
    raise ValueError(f"unknown retriever {name!r} (expected 'dense' or 'hybrid')")


def run(retriever_name: str = "dense", k: int = 5) -> dict:
    retriever = make_retriever(retriever_name)
    results = [score_case(case, _search(retriever, case, k))
               for case in load_retrieval_golden()]

    by_type: dict[str, list[CaseResult]] = {}
    for r in results:
        by_type.setdefault(r.doc_type, []).append(r)

    def _agg(rs: list[CaseResult]) -> dict:
        return {
            "n": len(rs),
            f"hit@{k}": round(sum(r.hit for r in rs) / len(rs), 3),
            "mrr": round(sum(r.reciprocal_rank for r in rs) / len(rs), 3),
            "violations": sum(len(r.violations) for r in rs),
        }

    return {
        "retriever": retriever_name,
        "k": k,
        "overall": _agg(results),
        "by_doc_type": {t: _agg(rs) for t, rs in sorted(by_type.items())},
        "cases": [asdict(r) for r in results],
    }


def render_markdown(report: dict) -> str:
    k = report["k"]
    lines = [
        f"### Retrieval eval — `{report['retriever']}` retriever, k={k}",
        "",
        f"| bucket | n | hit@{k} | MRR | forbidden hits |",
        "|---|---|---|---|---|",
    ]
    o = report["overall"]
    lines.append(f"| **overall** | {o['n']} | {o[f'hit@{k}']} | {o['mrr']} | {o['violations']} |")
    for t, a in report["by_doc_type"].items():
        lines.append(f"| {t} | {a['n']} | {a[f'hit@{k}']} | {a['mrr']} | {a['violations']} |")
    misses = [c for c in report["cases"] if not c["hit"] or c["violations"]]
    if misses:
        lines += ["", "**Misses / violations:**"]
        for c in misses:
            what = "MISS" if not c["hit"] else f"forbidden: {c['violations']}"
            lines.append(f"- `{c['id']}` ({what}) — retrieved: {c['top_sections']}")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--retriever", default="dense")
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--json", help="also write the full report to this path")
    args = parser.parse_args()

    report = run(retriever_name=args.retriever, k=args.k)
    print(render_markdown(report))
    if args.json:
        with open(args.json, "w") as f:
            json.dump(report, f, indent=2)
        print(f"\n(full report -> {args.json})")


if __name__ == "__main__":
    main()
