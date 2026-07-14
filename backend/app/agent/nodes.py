"""Graph nodes. intake -> (ask | hard_route | retrieve -> pricing -> draft)."""

from __future__ import annotations

import json
import re

from langchain_core.messages import AIMessage, SystemMessage

from app.agent import guidelines, prompts
from app.agent.llm import drafting_model, intake_model
from app.agent.state import AgentState
from app.config import settings
from app.retrieval import get_retriever

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.M)


def _parse_intake(raw: str) -> dict:
    try:
        return json.loads(_FENCE.sub("", raw.strip()))
    except json.JSONDecodeError:
        # Model broke format: treat its text as a plain follow-up question.
        return {"action": "ask", "reply": raw.strip(), "slots": {},
                "flags": [], "hard_trigger": None}


def _tag_routing(level: str, categories: list[str]) -> None:
    """§6.3: LangSmith-tag every routing event (best-effort; no-op untraced)."""
    try:
        from langsmith.run_helpers import get_current_run_tree
        rt = get_current_run_tree()
        if rt is not None:
            rt.tags = (rt.tags or []) + [f"route={level}"] + [
                f"trigger={c}" for c in categories]
    except Exception:
        pass


def intake_node(state: AgentState) -> dict:
    last_user = next((m for m in reversed(state["messages"])
                      if m.type == "human"), None)
    det_hits = guidelines.scan_hard_triggers(last_user.content) if last_user else []

    msgs = ([SystemMessage(prompts.intake_system())] + state["messages"]
            + [SystemMessage(f"deterministic_hits: {json.dumps(det_hits)}\n"
                             f"slots_so_far: {json.dumps(state.get('slots', {}))}")])
    out = _parse_intake(intake_model().invoke(msgs).content)

    slots = {**state.get("slots", {}),
             **{k: v for k, v in (out.get("slots") or {}).items() if v is not None}}
    flags = out.get("flags") or state.get("flags", [])
    action = out.get("action", "ask")

    # Precedence guard (§6.3): a deterministic hard hit ends intake even if
    # the model disagreed. Categories come from the doc's lists either way.
    categories = [c for c, _ in det_hits]
    matched = [kw for _, kw in det_hits]
    if out.get("hard_trigger"):
        categories.append(out["hard_trigger"].get("category", "model-judgment"))
        matched.append(out["hard_trigger"].get("evidence", ""))
    if det_hits:
        action = "hard_route"

    level = "hard" if action == "hard_route" else ("flag" if flags else "clear")
    if level != "clear":
        _tag_routing(level, categories)

    return {"messages": [AIMessage(out.get("reply", ""))],
            "slots": slots, "flags": flags,
            "trigger": {"level": level, "categories": categories,
                        "matched": matched},
            "_action": action}


def hard_route_node(state: AgentState) -> dict:
    """§6.3 routing packet for the estimator; the client ack was already
    composed by the intake model (reply in messages)."""
    transcript = [f"{m.type}: {m.content}" for m in state["messages"]]
    return {"routing_packet": {
        "route": "hard",
        "triggers": state["trigger"].get("categories", []),
        "matched_text": state["trigger"].get("matched", []),
        "slots": state.get("slots", {}),
        "transcript": transcript,
    }}


def retrieve_node(state: AgentState) -> dict:
    r = get_retriever()
    s = state.get("slots", {})
    scope = str(s.get("scope", "basement"))
    tier = None
    tb = str(s.get("package_tier_budget", "") or "")
    for t in ("ESSENTIAL", "SUPERIOR", "SUPREME"):
        if t in tb.upper():
            tier = t
    accessory = "accessory" in scope.lower()

    def pack(hits):
        seen, out = set(), []
        for h in hits:
            if h.citation in seen:
                continue
            seen.add(h.citation)
            out.append({"citation": h.citation, "text": h.text})
        return out

    quote_q = (f"{scope} {s.get('gfa_sqft', '')} sqft "
               f"{'separate entrance' if s.get('separate_entrance') else ''} "
               f"{s.get('kitchen', '')} {tier or ''}")
    code_qs = ["minimum ceiling height basement rooms"]
    if s.get("bedrooms_egress"):
        code_qs.append("bedroom egress window requirements")
    if accessory:
        code_qs += ["fire separation between dwelling units",
                    "smoke alarms carbon monoxide alarms secondary suite",
                    "change of use second suite requirements"]
    code_hits = [h for q in code_qs for h in r.search_building_code(q, k=3)]

    retrieved = {
        "past_project_quote": pack(r.search_past_quotes(quote_q, k=6,
                                                        package_tier=tier)),
        "building_code": pack(code_hits),
        "builder_guideline": pack(r.search_guidelines(
            f"allowances rules of thumb {tier or ''} {scope}", k=4)),
    }
    if accessory:
        retrieved["zoning_bylaw"] = pack(
            r.search_zoning("additional residential unit basement apartment "
                            "requirements parking", k=3))
    return {"retrieved": retrieved}


def pricing_node(state: AgentState) -> dict:
    """Tavily spot-check for volatile materials (never vendor scraping)."""
    if not settings.tavily_api_key:
        return {"pricing": [{"note": "TAVILY_API_KEY not set — pricing "
                                     "spot-check skipped; draft from "
                                     "allowances/comparables only"}]}
    from tavily import TavilyClient
    s = state.get("slots", {})
    queries = ["luxury vinyl plank flooring installed price per sqft Ontario"]
    if s.get("kitchen") and str(s.get("kitchen")).lower() not in ("no", "none", "false"):
        queries.append("quartz countertop installed price per sqft Ontario")
    if s.get("bedrooms_egress"):
        queries.append("egress window installation cost Ontario")
    client = TavilyClient(api_key=settings.tavily_api_key)
    out = []
    for q in queries[:3]:
        try:
            resp = client.search(q, max_results=3, include_answer=True)
            out.append({"query": q, "answer": resp.get("answer"),
                        "results": [{"title": x["title"], "url": x["url"]}
                                    for x in resp.get("results", [])]})
        except Exception as e:
            out.append({"query": q, "error": str(e)})
    return {"pricing": out}


def draft_node(state: AgentState) -> dict:
    msgs = [SystemMessage(prompts.draft_system()),
            ("user", prompts.draft_user(state.get("slots", {}),
                                        state.get("flags", []),
                                        state.get("retrieved", {}),
                                        state.get("pricing", [])))]
    feedback = state.get("estimator_feedback")
    if feedback:
        msgs.append(("user",
                     "REVISION REQUEST from the reviewing estimator (not the "
                     "client). Previous draft:\n\n"
                     f"{state.get('draft') or '(none)'}\n\n"
                     f"Requested changes:\n{feedback}\n\n"
                     "Produce the complete revised quote, keeping the same "
                     "citation discipline."))
    draft = drafting_model().invoke(msgs).content
    packet = None
    if state.get("flags"):
        packet = {"route": "flag",
                  "triggers": [f["condition"] for f in state["flags"]],
                  "slots": state.get("slots", {})}
    return {"draft": draft, "routing_packet": packet,
            "estimator_feedback": None,
            "messages": [AIMessage("Draft quote prepared — routed to the "
                                   "estimator for review before anything "
                                   "reaches you. (Draft attached below.)\n\n"
                                   + draft)]}
