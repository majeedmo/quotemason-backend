from __future__ import annotations

from typing import Annotated, Any, TypedDict

from langgraph.graph.message import add_messages


class Trigger(TypedDict, total=False):
    level: str                     # "clear" | "flag" | "hard"
    categories: list[str]          # trigger categories fired
    matched: list[str]             # exact matched text/keywords (§6.3 packet)


class AgentState(TypedDict, total=False):
    messages: Annotated[list, add_messages]
    slots: dict[str, Any]          # §3 slot keys + property_location/contact
    trigger: Trigger
    flags: list[dict]              # [{condition, flag_text}] — §6.2
    retrieved: dict[str, list]     # doc_type -> [{citation, text}]
    pricing: list[dict]            # [{query, answer, results}]
    draft: str | None
    routing_packet: dict | None    # §6.3 estimator hand-off
    estimator_feedback: str | None  # set on a /revise invoke; cleared by draft
    _action: str                   # intake routing decision (internal)
