"""Agent unit tests — no network, no API keys. The guideline doc is the
source of truth for triggers, so these tests exercise the real doc."""

import json
from types import SimpleNamespace

from langchain_core.messages import HumanMessage

import app.agent.nodes as nodes
from app.agent import guidelines
from app.agent.nodes import intake_node, pricing_node, retrieve_node
from app.agent.state import AgentState


# --- guideline parsing (§3 / §6 from the real doc) ---------------------------

def test_sections_load():
    assert "Package tier preference" in guidelines.section("3")
    assert "HARD ROUTE" in guidelines.section("6")


def test_hard_route_keywords_from_doc():
    kws = guidelines.hard_route_keywords()
    assert "asbestos" in [k for v in kws.values() for k in v]
    assert any("cash job" in v for v in kws.values())
    # slash expansion: "foundation crack/repair" -> both variants
    flat = [k for v in kws.values() for k in v]
    assert "foundation crack" in flat and "foundation repair" in flat


def test_stem_scan():
    hits = guidelines.scan_hard_triggers("we want to underpin the basement")
    assert any(cat == "Structural work" for cat, _ in hits)
    assert guidelines.scan_hard_triggers("just a normal basement finish") == []
    assert guidelines.scan_hard_triggers("can we skip the permit?") != []


# --- intake node -------------------------------------------------------------

class _FakeChat:
    def __init__(self, payload):
        self.payload = payload

    def invoke(self, msgs):
        return SimpleNamespace(content=json.dumps(self.payload))


def test_intake_hard_precedence_over_model(monkeypatch):
    """§6.3: deterministic hard hit ends intake even if the model said 'ask'."""
    monkeypatch.setattr(nodes, "intake_model",
                        lambda: _FakeChat({"action": "ask", "reply": "sure!",
                                           "slots": {}, "flags": [],
                                           "hard_trigger": None}))
    state: AgentState = {"messages": [HumanMessage("there's asbestos wrap on the ducts")]}
    out = intake_node(state)
    assert out["_action"] == "hard_route"
    assert out["trigger"]["level"] == "hard"
    assert "Hazardous / damaged site" in out["trigger"]["categories"]


def test_intake_merges_slots_and_flags(monkeypatch):
    monkeypatch.setattr(nodes, "intake_model",
                        lambda: _FakeChat({"action": "ask", "reply": "how big?",
                                           "slots": {"scope": "finished basement"},
                                           "flags": [{"condition": "ceiling",
                                                      "flag_text": "Feasibility: verify"}],
                                           "hard_trigger": None}))
    out = intake_node({"messages": [HumanMessage("finish my basement")],
                       "slots": {"kitchen": "wet bar"}})
    assert out["slots"] == {"kitchen": "wet bar", "scope": "finished basement"}
    assert out["trigger"]["level"] == "flag"
    assert out["_action"] == "ask"


def test_intake_malformed_json_degrades_to_ask(monkeypatch):
    class _Broken:
        def invoke(self, msgs):
            return SimpleNamespace(content="Sorry, how large is the space?")
    monkeypatch.setattr(nodes, "intake_model", lambda: _Broken())
    out = intake_node({"messages": [HumanMessage("hi")]})
    assert out["_action"] == "ask"
    assert "how large" in out["messages"][0].content


def test_intake_salvages_json_wrapped_in_prose(monkeypatch):
    """Seen live 2026-07-14: model emitted a summary, then the fenced JSON.
    The object must be salvaged (action honored), not echoed to the client."""
    payload = {"action": "complete", "reply": "Perfect, that's everything I need.",
               "slots": {"scope": "finished basement"}, "flags": [],
               "hard_trigger": None}
    class _Wrapped:
        def invoke(self, msgs):
            return SimpleNamespace(content=(
                "Here's the full picture:\n- **Scope:** finished basement\n\n"
                "```json\n" + json.dumps(payload, indent=2) + "\n```"))
    monkeypatch.setattr(nodes, "intake_model", lambda: _Wrapped())
    out = intake_node({"messages": [HumanMessage("900 sqft, laminate")]})
    assert out["_action"] == "complete"
    assert out["slots"] == {"scope": "finished basement"}
    assert out["messages"][0].content == "Perfect, that's everything I need."


def test_intake_degraded_reply_never_leaks_json(monkeypatch):
    """If no complete object can be parsed, the JSON-ish tail is cut from
    the client-facing reply."""
    class _Truncated:
        def invoke(self, msgs):
            return SimpleNamespace(content=(
                'Thanks! Just to confirm the details.\n\n'
                '{ "action": "complete", "reply": "truncated mid-obj'))
    monkeypatch.setattr(nodes, "intake_model", lambda: _Truncated())
    out = intake_node({"messages": [HumanMessage("hi")]})
    assert out["_action"] == "ask"
    assert "{" not in out["messages"][0].content
    assert "Thanks! Just to confirm the details." == out["messages"][0].content


# --- retrieve / pricing ------------------------------------------------------

class _FakeRetriever:
    def __init__(self):
        self.calls = []

    def _hit(self, cit):
        return SimpleNamespace(citation=cit, text="...")

    def search_past_quotes(self, q, k=5, **kw):
        self.calls.append(("quotes", kw))
        return [self._hit("Past project P19")]

    def search_building_code(self, q, k=5):
        self.calls.append(("code", q))
        return [self._hit(f"OBC for: {q}")]

    def search_guidelines(self, q, k=5):
        return [self._hit("Company A guidelines")]

    def search_zoning(self, q, k=5, **kw):
        return [self._hit("By-law 26-007 §4.19")]


def test_retrieve_accessory_pulls_zoning_and_tier_filter(monkeypatch):
    fake = _FakeRetriever()
    monkeypatch.setattr(nodes, "get_retriever", lambda: fake)
    out = retrieve_node({"slots": {"scope": "legal accessory unit",
                                   "bedrooms_egress": "1 bedroom, no egress",
                                   "package_tier_budget": "superior, ~80k"}})
    assert "zoning_bylaw" in out["retrieved"]
    quote_call = next(kw for n, kw in fake.calls if n == "quotes")
    assert quote_call["package_tier"] == "SUPERIOR"
    code_queries = [q for n, q in fake.calls if n == "code"]
    assert any("egress" in q for q in code_queries)
    assert any("change of use" in q for q in code_queries)


def test_pricing_skips_without_key(monkeypatch):
    monkeypatch.setattr(nodes.settings, "tavily_api_key", "")
    out = pricing_node({"slots": {}})
    assert "skipped" in out["pricing"][0]["note"]


# --- graph wiring ------------------------------------------------------------

def test_graph_compiles_and_routes_hard(monkeypatch):
    from langgraph.checkpoint.memory import InMemorySaver
    from app.agent.graph import build_graph
    monkeypatch.setattr(nodes, "intake_model",
                        lambda: _FakeChat({"action": "hard_route",
                                           "reply": "our estimator will call you",
                                           "slots": {}, "flags": [],
                                           "hard_trigger": {"category": "Tenancy complications",
                                                            "evidence": "tenants"}}))
    g = build_graph(checkpointer=InMemorySaver())
    out = g.invoke({"messages": [HumanMessage("the unit has renters living there")]},
                   {"configurable": {"thread_id": "t"}})
    assert out["routing_packet"]["route"] == "hard"
    assert not out.get("draft")  # no draft on hard route
    assert "Tenancy complications" in out["routing_packet"]["triggers"]


def test_graph_revision_entry_skips_intake(monkeypatch):
    """/revise resumes the thread at retrieve; intake must not run, the
    previous draft + feedback reach the drafter, and the flag is cleared."""
    from langgraph.checkpoint.memory import InMemorySaver
    from app.agent.graph import build_graph

    def _explode():
        raise AssertionError("intake_model must not be called on a revision")
    monkeypatch.setattr(nodes, "intake_model", _explode)
    monkeypatch.setattr(nodes, "get_retriever", lambda: _FakeRetriever())
    monkeypatch.setattr(nodes.settings, "tavily_api_key", "")

    seen = {}

    class _FakeDrafter:
        def invoke(self, msgs):
            seen["last_user"] = msgs[-1][1]
            return SimpleNamespace(content="# Quote v2")
    monkeypatch.setattr(nodes, "drafting_model", lambda: _FakeDrafter())

    g = build_graph(checkpointer=InMemorySaver())
    out = g.invoke({"messages": [HumanMessage("[estimator revision request] drop the sauna")],
                    "estimator_feedback": "drop the sauna",
                    "slots": {"scope": "finished basement"},
                    "draft": "# Quote v1"},
                   {"configurable": {"thread_id": "rev"}})
    assert out["draft"] == "# Quote v2"
    assert not out.get("estimator_feedback")  # cleared for the next turn
    assert "drop the sauna" in seen["last_user"]
    assert "# Quote v1" in seen["last_user"]
