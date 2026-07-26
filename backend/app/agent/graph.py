"""Agent graph: intake -> (END | hard_route -> END | codes -> takeoff ->
verify_takeoff -> [retry takeoff once, else] price_fill -> draft -> END). The
drafting pipeline mirrors how an estimator works — applicable codes, then
quantities (cross-checked against intake + itself), then prices — and each
stage's structured output is persisted for the quote-accuracy eval. Memory =
LangGraph checkpointer; Upstash Redis when configured (the hard memory
requirement), in-memory fallback for local dev/tests."""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from app.agent.nodes import (codes_node, draft_node, hard_route_node,
                             intake_node, price_fill_node, takeoff_node,
                             verify_takeoff_node)
from app.agent.state import AgentState
from app.config import settings

# 1 initial takeoff attempt + 1 verifier-triggered retry, then proceed
# regardless -- price_fill_node neutralizes whatever's still flagged rather
# than looping again, bounding worst-case added latency/cost to ~2x the
# takeoff stage.
MAX_TAKEOFF_VERIFY_ATTEMPTS = 2


def _route_after_verify_takeoff(state: dict) -> str:
    if (state.get("takeoff_issues")
            and state.get("takeoff_verify_attempts", 0) < MAX_TAKEOFF_VERIFY_ATTEMPTS):
        return "retry"
    return "proceed"


def _entry(state: dict) -> str:
    """Estimator revision requests (/revise) skip intake — the slots are
    already filled and the feedback isn't a customer message. Revisions
    re-run ALL three stages: feedback routinely changes scope, which
    invalidates the codes checklist and takeoff, and the persisted stage
    outputs must stay consistent with the revised draft."""
    return "codes" if state.get("estimator_feedback") else "intake"


def _route_after_intake(state: dict) -> str:
    action = state.get("_action", "ask")
    if action == "hard_route":
        return "hard_route"
    if action == "complete":
        return "codes"
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
    g.add_node("codes", codes_node)
    g.add_node("takeoff", takeoff_node)
    g.add_node("verify_takeoff", verify_takeoff_node)
    g.add_node("price_fill", price_fill_node)
    g.add_node("draft", draft_node)

    g.add_conditional_edges(START, _entry,
                            {"intake": "intake", "codes": "codes"})
    g.add_conditional_edges("intake", _route_after_intake,
                            {"hard_route": "hard_route",
                             "codes": "codes", END: END})
    g.add_edge("hard_route", END)
    g.add_edge("codes", "takeoff")
    g.add_edge("takeoff", "verify_takeoff")
    g.add_conditional_edges("verify_takeoff", _route_after_verify_takeoff,
                            {"retry": "takeoff", "proceed": "price_fill"})
    g.add_edge("price_fill", "draft")
    g.add_edge("draft", END)
    return g.compile(checkpointer=checkpointer or make_checkpointer())
