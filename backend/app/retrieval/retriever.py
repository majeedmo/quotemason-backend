"""Filtered dense retrieval over the shared Qdrant collection.

CLAUDE.md RAG architecture: ONE collection (settings.qdrant_collection),
filtered by jurisdiction + doc_type at query time — never a separate index
per municipality. This is the default retriever; the Task 6 upgrade adds
hybrid dense + BM25 alongside it.

Payload layout matches what langchain-qdrant wrote at ingestion (ingest.py):
chunk text under `page_content`, chunk metadata under `metadata.*`.

The local-path Qdrant fallback (no QDRANT_URL set) holds an exclusive file
lock — one process at a time; don't run retrieval and ingestion concurrently
in local mode.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache

from langchain_openai import OpenAIEmbeddings
from qdrant_client import QdrantClient
from qdrant_client.http.models import FieldCondition, Filter, MatchValue

from app.config import settings


@dataclass
class RetrievedChunk:
    text: str
    score: float
    metadata: dict = field(default_factory=dict)

    @property
    def citation(self) -> str:
        """Human-readable source line, doc_type-aware — what the drafter
        attaches to a code-driven or price line item."""
        m = self.metadata
        num = m.get("section_number", "")
        title = m.get("section_title", "")
        ver = m.get("source_version", "")
        dt = m.get("doc_type")
        if dt == "building_code":
            return f"OBC {num} — {title} | {ver}"
        if dt == "zoning_bylaw":
            return f"Cambridge Zoning By-law 26-007 §{num} — {title} | {ver}"
        if dt == "past_project_quote":
            syn = " (SYNTHETIC)" if m.get("synthetic") else ""
            return (f"Past project {m.get('project_code', '?')}{syn} — "
                    f"{m.get('city', '?')}, {m.get('package_tier', '?')} package — {title}")
        return f"Company A builder guidelines — {num} {title}".replace("—  ", "— ")


def _conditions(meta_filters: dict) -> list[FieldCondition]:
    """None-valued filters mean 'no constraint', so optional arguments can be
    passed straight through."""
    return [FieldCondition(key=f"metadata.{k}", match=MatchValue(value=v))
            for k, v in meta_filters.items() if v is not None]


def _default_client() -> QdrantClient:
    if settings.qdrant_url:
        return QdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key or None)
    return QdrantClient(path=str(settings.qdrant_local_path))


class CorpusRetriever:
    def __init__(self, client: QdrantClient | None = None,
                 embeddings: OpenAIEmbeddings | None = None):
        self.client = client or _default_client()
        self.embeddings = embeddings or OpenAIEmbeddings(
            model=settings.embedding_model, api_key=settings.openai_api_key)
        self.collection = settings.qdrant_collection

    def search(self, query: str, *, k: int = 5,
               must: dict | None = None,
               must_not: dict | None = None) -> list[RetrievedChunk]:
        flt = None
        if must or must_not:
            flt = Filter(must=_conditions(must or {}),
                         must_not=_conditions(must_not or {}))
        vec = self.embeddings.embed_query(query)
        pts = self.client.query_points(
            self.collection, query=vec, limit=k,
            query_filter=flt, with_payload=True).points
        return [RetrievedChunk(text=p.payload.get("page_content", ""),
                               score=p.score,
                               metadata=p.payload.get("metadata", {}))
                for p in pts]

    # Domain helpers — one per doc_type, matching the agent's tools.

    def search_building_code(self, query: str, k: int = 5) -> list[RetrievedChunk]:
        """OBC Part 9 (jurisdiction: ontario — province-wide, ingested once)."""
        return self.search(query, k=k,
                           must={"doc_type": "building_code", "jurisdiction": "ontario"})

    def search_zoning(self, query: str, k: int = 5,
                      jurisdiction: str = "cambridge") -> list[RetrievedChunk]:
        """Zoning bylaw — the per-municipality layer (Cambridge only this build)."""
        return self.search(query, k=k,
                           must={"doc_type": "zoning_bylaw", "jurisdiction": jurisdiction})

    def search_guidelines(self, query: str, k: int = 5) -> list[RetrievedChunk]:
        """Company A rules of thumb, tier allowances, labor bands, §6 triggers."""
        return self.search(query, k=k, must={"doc_type": "builder_guideline"})

    def search_past_quotes(self, query: str, k: int = 5, *,
                           city: str | None = None,
                           package_tier: str | None = None,
                           scope: str | None = None,
                           include_synthetic: bool = True) -> list[RetrievedChunk]:
        """Comparable past projects. Frontmatter vocabulary: package_tier is
        ESSENTIAL/SUPERIOR/SUPREME; scope e.g. 'basement', 'finished_basement',
        'accessory_unit'; city is title-case ('Guelph', 'Oakville')."""
        must = {"doc_type": "past_project_quote",
                "city": city, "package_tier": package_tier, "scope": scope}
        must_not = None if include_synthetic else {"synthetic": True}
        return self.search(query, k=k, must=must, must_not=must_not)


@lru_cache(maxsize=1)
def get_retriever():
    """The retriever the agent uses. Dispatches on settings.retriever so the
    hybrid upgrade is a config flip; the deferred import avoids a cycle
    (hybrid imports CorpusRetriever)."""
    if settings.retriever == "hybrid":
        from app.retrieval.hybrid import get_hybrid_retriever
        return get_hybrid_retriever()
    return CorpusRetriever()
