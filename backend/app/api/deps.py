"""Process-wide singletons for the API (graph + quote store)."""

from __future__ import annotations

from functools import lru_cache


@lru_cache(maxsize=1)
def get_graph():
    from app.agent.graph import build_graph
    return build_graph()


@lru_cache(maxsize=1)
def get_store():
    from app.quotes.store import QuoteStore
    store = QuoteStore()
    store.ensure_schema()
    return store
