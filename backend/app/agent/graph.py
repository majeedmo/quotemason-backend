"""Agent graph: intake -> (END | hard_route -> END | retrieve -> pricing ->
draft -> END). Memory = LangGraph checkpointer; Upstash Redis when configured
(the hard memory requirement), in-memory fallback for local dev/tests."""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from app.agent.nodes import (draft_node, hard_route_node, intake_node,
                             pricing_node, retrieve_node)
from app.agent.state import AgentState
from app.config import settings


def _entry(state: dict) -> str:
    """Estimator revision requests (/revise) skip intake — the slots are
    already filled and the feedback isn't a customer message."""
    return "retrieve" if state.get("estimator_feedback") else "intake"


def _route_after_intake(state: dict) -> str:
    action = state.get("_action", "ask")
    if action == "hard_route":
        return "hard_route"
    if action == "complete":
        return "retrieve"
    return END


def make_checkpointer():
    if settings.upstash_redis_url:
        # Not the official RedisSaver: it needs RediSearch (FT.*), which
        # Upstash doesn't support. See redis_checkpointer.py.
        from app.agent.redis_checkpointer import UpstashRedisSaver
        return UpstashRedisSaver(settings.upstash_redis_url)
    from langgraph.checkpoint.memory import InMemorySaver
    return InMemorySaver()


def build_graph(checkpointer=None):
    g = StateGraph(AgentState)
    g.add_node("intake", intake_node)
    g.add_node("hard_route", hard_route_node)
    g.add_node("retrieve", retrieve_node)
    g.add_node("pricing", pricing_node)
    g.add_node("draft", draft_node)

    g.add_conditional_edges(START, _entry,
                            {"intake": "intake", "retrieve": "retrieve"})
    g.add_conditional_edges("intake", _route_after_intake,
                            {"hard_route": "hard_route",
                             "retrieve": "retrieve", END: END})
    g.add_edge("hard_route", END)
    g.add_edge("retrieve", "pricing")
    g.add_edge("pricing", "draft")
    g.add_edge("draft", END)
    return g.compile(checkpointer=checkpointer or make_checkpointer())
