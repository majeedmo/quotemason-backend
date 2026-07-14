"""Retrieval smoke CLI (mirrors app.ingestion.ingest's role for retrieval).

Usage (from backend/):
    uv run python -m app.retrieval.smoke_test "bedroom egress window" --doc-type building_code
    uv run python -m app.retrieval.smoke_test "finished basement Oakville" \
        --doc-type past_project_quote --tier ESSENTIAL --no-synthetic
"""

from __future__ import annotations

import argparse

from app.retrieval.retriever import get_retriever


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("query")
    ap.add_argument("--doc-type", choices=["building_code", "past_project_quote",
                                           "builder_guideline", "zoning_bylaw"])
    ap.add_argument("--jurisdiction")
    ap.add_argument("--city")
    ap.add_argument("--tier", help="ESSENTIAL / SUPERIOR / SUPREME")
    ap.add_argument("--scope")
    ap.add_argument("--no-synthetic", action="store_true")
    ap.add_argument("-k", type=int, default=5)
    ap.add_argument("--full", action="store_true", help="print full chunk text")
    args = ap.parse_args()

    r = get_retriever()
    if args.doc_type == "past_project_quote":
        hits = r.search_past_quotes(args.query, k=args.k, city=args.city,
                                    package_tier=args.tier, scope=args.scope,
                                    include_synthetic=not args.no_synthetic)
    else:
        must = {k: v for k, v in {"doc_type": args.doc_type,
                                  "jurisdiction": args.jurisdiction}.items() if v}
        hits = r.search(args.query, k=args.k, must=must or None)

    for h in hits:
        print(f"\n[{h.score:.3f}] {h.citation}")
        print(h.text if args.full else h.text[:240].replace("\n", " "))

    r.client.close()


if __name__ == "__main__":
    main()
