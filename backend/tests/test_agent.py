"""Agent unit tests — no network, no API keys. The guideline doc is the
source of truth for triggers, so these tests exercise the real doc."""

import json
from types import SimpleNamespace

from langchain_core.messages import HumanMessage

import app.agent.nodes as nodes
from app.agent import guidelines
from app.agent.nodes import (codes_node, intake_node, price_fill_node,
                             takeoff_node)
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


# --- pipeline stages ---------------------------------------------------------

class _FakeRetriever:
    def __init__(self):
        self.calls = []

    def _hit(self, cit, **meta):
        return SimpleNamespace(citation=cit, text="...", metadata=meta)

    def search_past_quotes(self, q, k=5, **kw):
        self.calls.append(("quotes", kw))
        return [self._hit("Past project P19")]

    def search_building_code(self, q, k=5):
        self.calls.append(("code", q))
        return [self._hit(f"OBC for: {q}", doc_type="building_code")]

    def search_guidelines(self, q, k=5, **kw):
        return [self._hit("Company A guidelines")]

    def search_zoning(self, q, k=5, **kw):
        return [self._hit("By-law 26-007 §4.19", doc_type="zoning_bylaw")]


class _FakeStageModel:
    """Scripted stage model: pops canned responses; bind_tools is a no-op so
    the same fake serves the codes (tool-bound) and takeoff stages."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.prompts = []

    def bind_tools(self, tools):
        return self

    def invoke(self, msgs):
        self.prompts.append(msgs)
        return self.responses.pop(0)


def _codes_json():
    return json.dumps({
        "zoning_jurisdiction": "cambridge",
        "items": [{"requirement": "egress window in bedroom",
                   "citation": "OBC for: bedroom egress window requirements",
                   "doc_type": "building_code", "section_number": "9.9.10",
                   "applies_because": "1 bedroom, no egress",
                   "action": "line_item"}],
        "notes": ""})


def _patch_stage_retrievers(monkeypatch):
    from app.tools import regulatory
    fake = _FakeRetriever()
    monkeypatch.setattr(nodes, "get_retriever", lambda: fake)
    monkeypatch.setattr(regulatory, "get_retriever", lambda: fake)
    return fake


def test_codes_node_validates_checklist_and_packs_context(monkeypatch):
    fake = _patch_stage_retrievers(monkeypatch)
    model = _FakeStageModel(SimpleNamespace(content=_codes_json(), tool_calls=[]))
    monkeypatch.setattr(nodes, "codes_model", lambda: model)
    out = codes_node({"slots": {"scope": "legal accessory unit",
                                "bedrooms_egress": "1 bedroom, no egress"}})
    assert out["codes_checklist"]["items"][0]["action"] == "line_item"
    assert "zoning_bylaw" in out["retrieved"]  # accessory seeds pulled zoning
    code_queries = [q for n, q in fake.calls if n == "code"]
    assert any("egress" in q for q in code_queries)
    assert any("change of use" in q for q in code_queries)


def test_codes_node_runs_tool_calls_then_answers(monkeypatch):
    _patch_stage_retrievers(monkeypatch)
    model = _FakeStageModel(
        SimpleNamespace(content="", tool_calls=[
            {"name": "building_code_lookup", "id": "t1",
             "args": {"query": "wet bar sink venting"}}]),
        SimpleNamespace(content=_codes_json(), tool_calls=[]))
    monkeypatch.setattr(nodes, "codes_model", lambda: model)
    out = codes_node({"slots": {"scope": "basement", "kitchen": "wet bar"}})
    # the tool result row landed in the packed draft context
    cits = [r["citation"] for r in out["retrieved"]["building_code"]]
    assert "OBC for: wet bar sink venting" in cits
    assert out["codes_checklist"]["items"]


def test_codes_node_degrades_to_deterministic_seed_checklist(monkeypatch):
    _patch_stage_retrievers(monkeypatch)
    model = _FakeStageModel(
        SimpleNamespace(content="I cannot produce JSON right now", tool_calls=[]),
        SimpleNamespace(content="still prose", tool_calls=[]))
    monkeypatch.setattr(nodes, "codes_model", lambda: model)
    out = codes_node({"slots": {"scope": "basement"}})
    cl = out["codes_checklist"]
    assert cl["items"] and all(i["action"] == "verify_on_site" for i in cl["items"])
    assert "deterministic fallback" in cl["notes"]


def test_takeoff_node_filters_comparables_by_tier_and_validates(monkeypatch):
    fake = _patch_stage_retrievers(monkeypatch)
    takeoff_json = json.dumps({
        "gfa_sqft": 900,
        "lines": [{"category": "flooring", "item": "lvp",
                   "description": "LVP flooring", "quantity": 945,
                   "unit": "sqft", "basis": "GFA 900 + 5% waste (§4)",
                   "source": "guideline_s4", "comparable_ref": ""}],
        "assumptions": ["subfloor assumed level"]})
    model = _FakeStageModel(SimpleNamespace(content=takeoff_json))
    monkeypatch.setattr(nodes, "drafting_model", lambda: model)
    out = takeoff_node({"slots": {"scope": "finished basement",
                                  "gfa_sqft": 900,
                                  "package_tier_budget": "superior, ~80k"},
                        "codes_checklist": {"items": []}})
    quote_call = next(kw for n, kw in fake.calls if n == "quotes")
    assert quote_call["package_tier"] == "SUPERIOR"
    assert out["takeoff"]["lines"][0]["quantity"] == 945
    assert out["retrieved"]["past_project_quote"][0]["citation"] == "Past project P19"


def test_takeoff_node_degrades_to_none_on_unparseable_output(monkeypatch):
    _patch_stage_retrievers(monkeypatch)
    model = _FakeStageModel(SimpleNamespace(content="no json"),
                            SimpleNamespace(content="still no json"))
    monkeypatch.setattr(nodes, "drafting_model", lambda: model)
    out = takeoff_node({"slots": {"scope": "basement"}})
    assert out["takeoff"] is None


def _takeoff_state(*lines):
    return {"takeoff": {"lines": list(lines)}}


def test_price_fill_prices_fresh_sheet_rows_with_arithmetic(monkeypatch):
    monkeypatch.setattr(nodes.settings, "tavily_api_key", "")
    out = price_fill_node(_takeoff_state(
        {"category": "flooring", "item": "lvp", "description": "LVP",
         "quantity": 100, "unit": "sqft"}))
    row = out["price_resolution"][0]
    assert row["price_source"] == "price_sheet"
    assert row["extended_low_cad"] == round(100 * row["unit_price_low_cad"], 2)
    assert not row["stale"] and "updated" in row["source_detail"]


def test_price_fill_unpriced_note_when_missing_and_no_key(monkeypatch):
    monkeypatch.setattr(nodes.settings, "tavily_api_key", "")
    out = price_fill_node(_takeoff_state(
        {"category": "flooring", "item": "unobtainium", "quantity": 1,
         "unit": "each"}))
    row = out["price_resolution"][0]
    assert row["price_source"] == "unpriced" and row["sheet_status"] == "missing"
    assert "estimator to price" in row["note"]


def test_price_fill_stale_row_falls_back_to_tavily(monkeypatch):
    import sys
    from datetime import date
    from app.pricing.materials import PriceRow
    stale = PriceRow(category="flooring", item="lvp", unit="per_sqft_cad",
                     price_low_cad=4.5, price_high_cad=7.0,
                     updated_at=date(2020, 1, 1), source="old list")
    monkeypatch.setattr(nodes.materials, "lookup", lambda c, i: stale)
    monkeypatch.setattr(nodes.settings, "tavily_api_key", "k")

    class _FakeTavily:
        def __init__(self, api_key):
            pass

        def search(self, q, **kw):
            return {"answer": "$5-6/sqft installed",
                    "results": [{"title": "src", "url": "http://x"}]}
    monkeypatch.setitem(sys.modules, "tavily",
                        SimpleNamespace(TavilyClient=_FakeTavily))
    out = price_fill_node(_takeoff_state(
        {"category": "flooring", "item": "lvp", "quantity": 100,
         "unit": "sqft"}))
    row = out["price_resolution"][0]
    assert row["price_source"] == "tavily" and row["sheet_status"] == "stale"
    assert row["answer"].startswith("$5-6")


def test_price_fill_empty_takeoff_yields_no_rows(monkeypatch):
    monkeypatch.setattr(nodes.settings, "tavily_api_key", "")
    assert price_fill_node({"takeoff": None}) == {"price_resolution": []}


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
    """/revise resumes the thread at the codes stage; intake must not run,
    all three stages re-run (fresh checklist/takeoff — feedback can change
    scope), the previous draft + feedback reach the drafter, and the flag
    is cleared."""
    from langgraph.checkpoint.memory import InMemorySaver
    from app.agent.graph import build_graph

    def _explode():
        raise AssertionError("intake_model must not be called on a revision")
    monkeypatch.setattr(nodes, "intake_model", _explode)
    _patch_stage_retrievers(monkeypatch)
    monkeypatch.setattr(nodes.settings, "tavily_api_key", "")
    monkeypatch.setattr(
        nodes, "codes_model",
        lambda: _FakeStageModel(
            SimpleNamespace(content=_codes_json(), tool_calls=[])))

    seen = {}

    class _FakeDrafter:
        """Serves takeoff (JSON-retry path -> None) and draft stages."""
        def invoke(self, msgs):
            last = msgs[-1]
            seen["last_user"] = last[1] if isinstance(last, tuple) else last.content
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
    # stages re-ran: fresh checklist from the codes stage this invoke
    assert out["codes_checklist"]["items"][0]["section_number"] == "9.9.10"
