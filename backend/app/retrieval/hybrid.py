"""Hybrid dense + BM25 retrieval (Task 6 advanced retriever).

Dense semantic search under-retrieves the exact clause numbers and defined
terms that legal/code text turns on ("9.41", "change of use", "front yard
setback") — the Task 5 retrieval baseline missed exactly those keyword/table
cases. This adds a lexical BM25 index over the SAME chunks and fuses the two
rankings with Reciprocal Rank Fusion.

Design (in-process, no Qdrant change):
- the dense side is the existing ``CorpusRetriever`` — reused, not reimplemented,
  so the dense baseline stays byte-for-byte identical for the 6b comparison;
- the BM25 index is built from ``build_chunks()`` (deterministic, offline) — the
  same chunks ingestion embedded, so lexical and dense hits align 1:1 by text;
- the tokenizer keeps dotted identifiers intact ("9.9.10.1" is ONE token), which
  is the whole reason BM25 helps here;
- every query goes through both retrievers with the identical metadata filters,
  then RRF fuses them — no score normalization needed across the two scales.

The public surface mirrors ``CorpusRetriever`` (same four doc_type helpers,
same signatures) so the eval harness and the agent can swap retrievers freely.
"""

from __future__ import annotations

import re
from functools import lru_cache

from app.retrieval.retriever import CorpusRetriever, RetrievedChunk

# Keep dotted clause/defined-term identifiers whole: "9.9.10.1" and "26-007"
# must survive tokenization or BM25 can't match on the thing that matters most.
_TOKEN = re.compile(r"[a-z0-9]+(?:[.\-][a-z0-9]+)*")

RRF_C = 60  # standard Reciprocal Rank Fusion constant


def _tokenize(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


def _match_meta(metadata: dict, must: dict | None, must_not: dict | None) -> bool:
    """In-Python mirror of the Qdrant filter (`retriever._conditions`): None-valued
    constraints are skipped, `must` is AND, `must_not` excludes on any match."""
    for key, val in (must or {}).items():
        if val is not None and metadata.get(key) != val:
            return False
    for key, val in (must_not or {}).items():
        if val is not None and metadata.get(key) == val:
            return False
    return True


class HybridRetriever:
    """Dense (Qdrant) + BM25 (in-process) fused via RRF.

    ``dense`` and ``chunks`` are injectable for no-network unit tests; in
    production both default to the real dense retriever and the real corpus.
    """

    def __init__(self, dense: CorpusRetriever | None = None, chunks=None):
        from rank_bm25 import BM25Okapi

        self.dense = dense or CorpusRetriever()
        if chunks is None:
            from app.ingestion.ingest import build_chunks
            chunks = build_chunks()
        self._chunks = list(chunks)
        self._bm25 = BM25Okapi([_tokenize(c.text) for c in self._chunks])

    # --- BM25 half -----------------------------------------------------------

    def _bm25_search(self, query: str, k: int,
                     must: dict | None, must_not: dict | None) -> list[RetrievedChunk]:
        tokens = _tokenize(query)
        if not tokens:
            return []
        scores = self._bm25.get_scores(tokens)
        scored = [
            (float(scores[i]), c)
            for i, c in enumerate(self._chunks)
            if scores[i] > 0 and _match_meta(c.metadata, must, must_not)
        ]
        scored.sort(key=lambda t: t[0], reverse=True)
        return [RetrievedChunk(text=c.text, score=s, metadata=c.metadata)
                for s, c in scored[:k]]

    # --- fusion --------------------------------------------------------------

    @staticmethod
    def _rrf(ranked_lists: list[list[RetrievedChunk]], k: int) -> list[RetrievedChunk]:
        fused: dict[str, float] = {}
        rep: dict[str, RetrievedChunk] = {}
        for hits in ranked_lists:
            for rank, hit in enumerate(hits, start=1):
                key = hit.text  # dense.text == bm25.text for the same chunk
                fused[key] = fused.get(key, 0.0) + 1.0 / (RRF_C + rank)
                rep.setdefault(key, hit)
        top = sorted(fused, key=lambda kk: fused[kk], reverse=True)[:k]
        return [RetrievedChunk(text=rep[kk].text, score=fused[kk],
                               metadata=rep[kk].metadata) for kk in top]

    def _hybrid_search(self, query: str, k: int,
                       must: dict | None = None,
                       must_not: dict | None = None) -> list[RetrievedChunk]:
        pool = max(4 * k, 20)  # fuse from a generous pool, return the top k
        dense = self.dense.search(query, k=pool, must=must, must_not=must_not)
        bm25 = self._bm25_search(query, k=pool, must=must, must_not=must_not)
        return self._rrf([dense, bm25], k)

    # --- public surface: mirrors CorpusRetriever -----------------------------

    def search(self, query: str, *, k: int = 5,
               must: dict | None = None,
               must_not: dict | None = None) -> list[RetrievedChunk]:
        return self._hybrid_search(query, k, must=must, must_not=must_not)

    def search_building_code(self, query: str, k: int = 5) -> list[RetrievedChunk]:
        return self._hybrid_search(
            query, k, must={"doc_type": "building_code", "jurisdiction": "ontario"})

    def search_zoning(self, query: str, k: int = 5,
                      jurisdiction: str = "cambridge") -> list[RetrievedChunk]:
        return self._hybrid_search(
            query, k, must={"doc_type": "zoning_bylaw", "jurisdiction": jurisdiction})

    def search_guidelines(self, query: str, k: int = 5) -> list[RetrievedChunk]:
        return self._hybrid_search(query, k, must={"doc_type": "builder_guideline"})

    def search_past_quotes(self, query: str, k: int = 5, *,
                           city: str | None = None,
                           package_tier: str | None = None,
                           scope: str | None = None,
                           include_synthetic: bool = True) -> list[RetrievedChunk]:
        must = {"doc_type": "past_project_quote",
                "city": city, "package_tier": package_tier, "scope": scope}
        must_not = None if include_synthetic else {"synthetic": True}
        return self._hybrid_search(query, k, must=must, must_not=must_not)


@lru_cache(maxsize=1)
def get_hybrid_retriever() -> HybridRetriever:
    return HybridRetriever()
