"""Agent unit tests — no network, no API keys. The guideline doc is the
source of truth for triggers, so these tests exercise the real doc."""

import json
from types import SimpleNamespace

import pytest
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


def _takeoff_state(*lines, gfa_sqft=None, slots=None):
    return {"takeoff": {"lines": list(lines), "gfa_sqft": gfa_sqft},
            "slots": slots or {}}


def test_price_fill_prices_fresh_sheet_rows_with_arithmetic(monkeypatch):
    monkeypatch.setattr(nodes.settings, "tavily_api_key", "")
    out = price_fill_node(_takeoff_state(
        {"category": "flooring", "item": "lvp", "description": "LVP",
         "quantity": 100, "unit": "sqft"}))
    row = out["price_resolution"][0]
    assert row["price_source"] == "price_sheet"
    assert row["extended_low_cad"] == round(100 * row["unit_price_low_cad"], 2)
    assert not row["stale"] and "updated" in row["source_detail"]
    # material prices have no size axis — quoted is always the midpoint
    assert row["unit_price_quoted_cad"] == pytest.approx(
        (row["unit_price_low_cad"] + row["unit_price_high_cad"]) / 2)
    assert row["extended_quoted_cad"] == round(100 * row["unit_price_quoted_cad"], 2)


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


def _labor_row(**overrides):
    from app.pricing.labor import LaborRow
    defaults = dict(trade="framing", job_size_band="small_lt_500sqft",
                    unit="per_sqft_floor", rate_low_cad=4.50, rate_high_cad=6.50,
                    includes="studs", status="VERIFIED", notes="")
    return LaborRow(**{**defaults, **overrides})


def test_price_fill_labor_quantity_based_multiplies_by_qty(monkeypatch):
    monkeypatch.setattr(nodes.settings, "tavily_api_key", "")
    monkeypatch.setattr(nodes.labor, "lookup", lambda t, b: _labor_row())
    out = price_fill_node(_takeoff_state(
        {"id": "t1", "category": "framing", "trade": "framing",
         "quantity": 100, "unit": "linear_ft"}))
    row = out["price_resolution"][0]
    assert row["price_source"] == "labor_rate"
    assert row["extended_low_cad"] == 450.0 and row["extended_high_cad"] == 650.0
    assert row["takeoff_line_ref"] == "t1"
    assert not row["rate_unverified"] and not row["site_dependent"]
    # no gfa_sqft in this takeoff -> no size axis -> quoted defaults to midpoint
    assert row["unit_price_quoted_cad"] == 5.50
    assert row["extended_quoted_cad"] == 550.0


def test_price_fill_labor_quoted_interpolates_within_band_by_gfa(monkeypatch):
    """End-to-end through the node: a per-unit rate reverses (near the
    band's top edge -> low rate), a lump-sum rate scales up (near the top
    edge -> high total) — both driven by the real takeoff gfa_sqft."""
    monkeypatch.setattr(nodes.settings, "tavily_api_key", "")

    def fake_lookup(trade, band):
        if trade == "framing":
            return _labor_row(unit="per_sqft_floor", rate_low_cad=4.50,
                              rate_high_cad=6.50, job_size_band=band)
        return _labor_row(trade="electrical_rough_and_finish", unit="lump_sum",
                          rate_low_cad=6000, rate_high_cad=10000, job_size_band=band)
    monkeypatch.setattr(nodes.labor, "lookup", fake_lookup)

    out = price_fill_node(_takeoff_state(
        {"id": "t1", "category": "framing", "trade": "framing",
         "quantity": 10, "unit": "linear_ft"},
        {"id": "t2", "category": "electrical", "trade": "electrical_rough_and_finish",
         "quantity": 1, "unit": "lump_sum"},
        gfa_sqft=490))  # near the top edge of the small_lt_500sqft band
    by_ref = {r["takeoff_line_ref"]: r for r in out["price_resolution"]}
    # per-unit: near the band's top -> reversed toward the LOW end
    assert by_ref["t1"]["unit_price_quoted_cad"] < 5.50
    # lump-sum: near the band's top -> scaled toward the HIGH end
    assert by_ref["t2"]["extended_quoted_cad"] > 8000


def test_price_fill_labor_lump_sum_ignores_quantity(monkeypatch):
    monkeypatch.setattr(nodes.settings, "tavily_api_key", "")
    monkeypatch.setattr(nodes.labor, "lookup", lambda t, b: _labor_row(
        trade="demolition_and_prep", unit="lump_sum",
        rate_low_cad=1500, rate_high_cad=3500))
    out = price_fill_node(_takeoff_state(
        {"id": "t1", "category": "demolition", "trade": "demolition_and_prep",
         "quantity": 900, "unit": "lump_sum"}))
    row = out["price_resolution"][0]
    # lump-sum bands are flat — the takeoff's arbitrary quantity must not scale it
    assert row["extended_low_cad"] == 1500 and row["extended_high_cad"] == 3500


def test_price_fill_labor_missing_trade_is_unpriced(monkeypatch):
    monkeypatch.setattr(nodes.settings, "tavily_api_key", "")
    monkeypatch.setattr(nodes.labor, "lookup", lambda t, b: None)
    out = price_fill_node(_takeoff_state(
        {"id": "t1", "category": "x", "trade": "no_such_trade",
         "quantity": 1, "unit": "each"}))
    row = out["price_resolution"][0]
    assert row["price_source"] == "unpriced" and "no labor rate" in row["note"]


def test_price_fill_labor_site_dependent_flag_propagates(monkeypatch):
    monkeypatch.setattr(nodes.settings, "tavily_api_key", "")
    monkeypatch.setattr(nodes.labor, "lookup", lambda t, b: _labor_row(
        trade="excavation_below_grade_entrance", unit="lump_sum",
        rate_low_cad=8000, rate_high_cad=15000, status="VERIFIED_SITE_DEPENDENT"))
    out = price_fill_node(_takeoff_state(
        {"id": "t1", "category": "excavation", "trade": "excavation_below_grade_entrance",
         "quantity": 1, "unit": "lump_sum"}, gfa_sqft=490))  # near band top -- must not matter
    row = out["price_resolution"][0]
    assert row["site_dependent"] is True and not row["rate_unverified"]
    # site-dependent rows always quote the midpoint, regardless of gfa position
    assert row["extended_quoted_cad"] == 11500


def test_price_fill_labor_placeholder_status_marks_rate_unverified(monkeypatch):
    monkeypatch.setattr(nodes.settings, "tavily_api_key", "")
    monkeypatch.setattr(nodes.labor, "lookup", lambda t, b: _labor_row(
        status="PLACEHOLDER_OWNER_VERIFY"))
    out = price_fill_node(_takeoff_state(
        {"id": "t1", "category": "framing", "trade": "framing",
         "quantity": 10, "unit": "linear_ft"}))
    assert out["price_resolution"][0]["rate_unverified"] is True


def test_price_fill_material_and_labor_share_takeoff_line_ref(monkeypatch):
    monkeypatch.setattr(nodes.settings, "tavily_api_key", "")
    monkeypatch.setattr(nodes.labor, "lookup", lambda t, b: _labor_row(
        trade="flooring_install_lvp", unit="per_sqft_floor",
        rate_low_cad=2.0, rate_high_cad=3.5))
    out = price_fill_node(_takeoff_state(
        {"id": "t7", "category": "flooring", "item": "lvp",
         "trade": "flooring_install_lvp", "quantity": 100, "unit": "sqft"}))
    rows = out["price_resolution"]
    assert len(rows) == 2  # material row + labor row, never blended
    sources = {r["price_source"] for r in rows}
    assert sources == {"price_sheet", "labor_rate"}
    assert all(r["takeoff_line_ref"] == "t7" for r in rows)


def test_price_fill_neither_item_nor_trade_is_unpriced(monkeypatch):
    monkeypatch.setattr(nodes.settings, "tavily_api_key", "")
    out = price_fill_node(_takeoff_state(
        {"id": "t1", "category": "misc", "quantity": 1, "unit": "lump_sum"}))
    row = out["price_resolution"][0]
    assert row["price_source"] == "unpriced"
    assert "no material or labor key" in row["note"]


def _allowance_row(**overrides):
    from app.pricing.allowances import AllowanceRow
    defaults = dict(category="kitchen", item="quartz_countertop", unit="per_sqft_cad",
                    essential="30", superior="45", supreme="45", status="GROUNDED",
                    source="real quotes 2026")
    return AllowanceRow(**{**defaults, **overrides})


def test_price_fill_allowance_hit_resolves_by_project_tier(monkeypatch):
    monkeypatch.setattr(nodes.settings, "tavily_api_key", "")
    monkeypatch.setattr(nodes.allowances, "lookup", lambda c, i: _allowance_row())
    out = price_fill_node(_takeoff_state(
        {"id": "t1", "category": "kitchen", "allowance_item": "quartz_countertop",
         "quantity": 40, "unit": "sqft"},
        slots={"package_tier_budget": "superior, ~90k"}))
    row = out["price_resolution"][0]
    assert row["price_source"] == "allowance"
    assert row["unit_price_quoted_cad"] == 45.0
    assert row["extended_quoted_cad"] == 1800.0
    assert row["source_detail"] == "real quotes 2026 (SUPERIOR tier)"


def test_price_fill_allowance_spec_only_tier_falls_back_to_material_sheet(monkeypatch):
    """vanity: ESSENTIAL has a real $ figure, SUPERIOR/SUPREME are spec-only
    in the allowances CSV — must fall back to the material sheet, not go
    straight to unpriced."""
    monkeypatch.setattr(nodes.settings, "tavily_api_key", "")
    monkeypatch.setattr(nodes.allowances, "lookup", lambda c, i: _allowance_row(
        category="bathroom", item="vanity", unit="per_unit_cad",
        essential="500", superior="floating + quartz top", supreme="floating + sconces"))

    class _FakeMaterialRow:
        price_low_cad, price_high_cad, unit, source = 500.0, 2000.0, "per_unit_cad", "web market research 2026"
        updated_at = __import__("datetime").date(2026, 7, 24)
    monkeypatch.setattr(nodes.materials, "lookup", lambda c, i: _FakeMaterialRow())

    out = price_fill_node(_takeoff_state(
        {"id": "t1", "category": "bathroom", "allowance_item": "vanity",
         "quantity": 1, "unit": "each"},
        slots={"package_tier_budget": "superior, ~90k"}))
    row = out["price_resolution"][0]
    assert row["price_source"] == "price_sheet"  # fell back, not "allowance"
    assert row["allowance_item"] == "vanity"
    assert row["item"] == "vanity"
    assert row["unit_price_quoted_cad"] == 1250.0  # midpoint of the material sheet fallback


def test_price_fill_tolerates_model_echoing_category_slash_item(monkeypatch):
    """Observed live: the takeoff model sometimes puts the full "category/item"
    pair (copied from the prompt's key list) into item/allowance_item instead
    of splitting it. price_fill must still resolve the lookup correctly --
    verified here by recording exactly what key each lookup was called with."""
    monkeypatch.setattr(nodes.settings, "tavily_api_key", "")
    material_calls, allowance_calls = [], []
    monkeypatch.setattr(nodes.materials, "lookup",
                        lambda c, i: material_calls.append((c, i)) or None)
    monkeypatch.setattr(nodes.allowances, "lookup",
                        lambda c, i: allowance_calls.append((c, i)) or _allowance_row())
    price_fill_node(_takeoff_state(
        {"id": "t1", "category": "flooring", "item": "flooring/lvp",
         "quantity": 100, "unit": "sqft"},
        {"id": "t2", "category": "kitchen", "allowance_item": "kitchen/quartz_countertop",
         "quantity": 40, "unit": "sqft"},
        slots={"package_tier_budget": "superior, ~90k"}))
    assert material_calls == [("flooring", "lvp")]
    assert allowance_calls == [("kitchen", "quartz_countertop")]


def test_price_fill_allowance_missing_row_falls_back_and_can_still_be_unpriced(monkeypatch):
    monkeypatch.setattr(nodes.settings, "tavily_api_key", "")
    monkeypatch.setattr(nodes.allowances, "lookup", lambda c, i: None)
    monkeypatch.setattr(nodes.materials, "lookup", lambda c, i: None)
    out = price_fill_node(_takeoff_state(
        {"id": "t1", "category": "bathroom", "allowance_item": "nonexistent",
         "quantity": 1, "unit": "each"},
        slots={"package_tier_budget": "essential, ~50k"}))
    row = out["price_resolution"][0]
    assert row["price_source"] == "unpriced"
    assert row["allowance_item"] == "nonexistent"


# --- traceability: code_item -> takeoff line -> price_resolution row --------

def test_codes_node_assigns_sequential_ids_never_from_model(monkeypatch):
    _patch_stage_retrievers(monkeypatch)
    payload = json.loads(_codes_json())
    payload["items"].append({**payload["items"][0], "requirement": "second item"})
    model = _FakeStageModel(SimpleNamespace(content=json.dumps(payload), tool_calls=[]))
    monkeypatch.setattr(nodes, "codes_model", lambda: model)
    out = codes_node({"slots": {"scope": "basement"}})
    ids = [i["id"] for i in out["codes_checklist"]["items"]]
    assert ids == ["c1", "c2"]


def test_takeoff_node_injects_synthetic_line_for_uncovered_mandatory_code_item(monkeypatch):
    _patch_stage_retrievers(monkeypatch)
    # the model's takeoff omits any line referencing the mandatory code item
    takeoff_json = json.dumps({"gfa_sqft": 900, "lines": [], "assumptions": []})
    model = _FakeStageModel(SimpleNamespace(content=takeoff_json))
    monkeypatch.setattr(nodes, "drafting_model", lambda: model)
    checklist = {"items": [{"id": "c1", "requirement": "egress window",
                            "citation": "OBC 9.9.10.1", "doc_type": "building_code",
                            "action": "line_item"}]}
    out = takeoff_node({"slots": {"scope": "finished basement"},
                        "codes_checklist": checklist})
    lines = out["takeoff"]["lines"]
    assert len(lines) == 1
    injected = lines[0]
    assert injected["code_item_ref"] == "c1" and injected["source"] == "code_item"
    assert "egress window" in injected["description"]


def test_takeoff_node_no_injection_when_code_item_covered(monkeypatch):
    _patch_stage_retrievers(monkeypatch)
    takeoff_json = json.dumps({"gfa_sqft": 900, "lines": [
        {"category": "windows", "item": "egress_window", "quantity": 1,
         "unit": "each", "source": "code_item", "code_item_ref": "c1"}],
        "assumptions": []})
    model = _FakeStageModel(SimpleNamespace(content=takeoff_json))
    monkeypatch.setattr(nodes, "drafting_model", lambda: model)
    checklist = {"items": [{"id": "c1", "requirement": "egress window",
                            "citation": "OBC 9.9.10.1", "doc_type": "building_code",
                            "action": "line_item"}]}
    out = takeoff_node({"slots": {"scope": "finished basement"},
                        "codes_checklist": checklist})
    assert len(out["takeoff"]["lines"]) == 1  # nothing injected — already covered
    assert out["takeoff"]["lines"][0]["id"] == "t1"  # ids assigned in code


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
