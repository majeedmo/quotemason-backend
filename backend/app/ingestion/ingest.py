"""Ingestion CLI: load corpus -> structure-aware chunks -> embed -> Qdrant upsert.

Usage (from backend/):
    uv run python -m app.ingestion.ingest --dry-run     # chunk + stats, no API calls
    uv run python -m app.ingestion.ingest               # embed + upsert (needs OPENAI_API_KEY)

One shared collection, filtered at query time by jurisdiction + doc_type
(CLAUDE.md RAG architecture — never a separate index per municipality).
Chunk IDs are deterministic, so re-running is an idempotent upsert.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter

from app.config import settings
from app.ingestion.chunking import Chunk, chunk_doc
from app.ingestion.loaders import load_all


def build_chunks() -> list[Chunk]:
    docs = load_all()
    chunks: list[Chunk] = []
    for d in docs:
        chunks.extend(chunk_doc(d))
    return chunks


def report(chunks: list[Chunk]) -> None:
    by_type = Counter(c.metadata.get("doc_type") for c in chunks)
    sizes = sorted(len(c.text) for c in chunks)
    print(f"chunks: {len(chunks)}  by doc_type: {dict(by_type)}")
    if chunks:
        mid = sizes[len(sizes) // 2]
        print(f"chars/chunk: min {sizes[0]}, median {mid}, max {sizes[-1]}")
    ids = [c.id for c in chunks]
    dupes = [i for i, n in Counter(ids).items() if n > 1]
    if dupes:
        print(f"ERROR: {len(dupes)} duplicate chunk ids", file=sys.stderr)
        sys.exit(1)


def upsert(chunks: list[Chunk]) -> None:
    from langchain_core.documents import Document
    from langchain_openai import OpenAIEmbeddings
    from langchain_qdrant import QdrantVectorStore
    from qdrant_client import QdrantClient
    from qdrant_client.http.models import Distance, PayloadSchemaType, VectorParams

    if not settings.openai_api_key:
        print("OPENAI_API_KEY is not set (backend/.env) — run with --dry-run or add the key.",
              file=sys.stderr)
        sys.exit(2)

    if settings.qdrant_url:
        client = QdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key or None)
    else:
        client = QdrantClient(path=str(settings.qdrant_local_path))

    if not client.collection_exists(settings.qdrant_collection):
        client.create_collection(
            collection_name=settings.qdrant_collection,
            vectors_config=VectorParams(size=1536, distance=Distance.COSINE),
        )

    # Qdrant Cloud (strict mode) rejects filters on unindexed payload fields;
    # cover every key the retriever filters on. Idempotent, like the upsert.
    filter_fields = {
        "doc_type": PayloadSchemaType.KEYWORD,
        "jurisdiction": PayloadSchemaType.KEYWORD,
        "city": PayloadSchemaType.KEYWORD,
        "package_tier": PayloadSchemaType.KEYWORD,
        "scope": PayloadSchemaType.KEYWORD,
        "synthetic": PayloadSchemaType.BOOL,
    }
    for field, schema in filter_fields.items():
        client.create_payload_index(
            collection_name=settings.qdrant_collection,
            field_name=f"metadata.{field}",
            field_schema=schema,
        )

    store = QdrantVectorStore(
        client=client,
        collection_name=settings.qdrant_collection,
        embedding=OpenAIEmbeddings(model=settings.embedding_model,
                                   api_key=settings.openai_api_key),
    )
    docs = [Document(page_content=c.text, metadata=c.metadata) for c in chunks]
    ids = [c.id for c in chunks]
    B = 128
    for i in range(0, len(docs), B):
        store.add_documents(docs[i:i + B], ids=ids[i:i + B])
        print(f"upserted {min(i + B, len(docs))}/{len(docs)}")
    print(f"done -> collection '{settings.qdrant_collection}'"
          f" @ {settings.qdrant_url or settings.qdrant_local_path}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="load + chunk + validate only; no embedding, no Qdrant")
    ap.add_argument("--show", metavar="N", type=int, default=0,
                    help="print the first N chunks (for eyeballing)")
    args = ap.parse_args()

    chunks = build_chunks()
    report(chunks)
    for c in chunks[: args.show]:
        print("\n" + "=" * 70)
        print(c.text[:600])
    if not args.dry_run:
        upsert(chunks)


if __name__ == "__main__":
    main()
