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
    monkeypatch.setattr(nodes, "takeoff_model", lambda: model)
    out = takeoff_node({"slots": {"scope": "finished basement",
                                  "gfa_sqft": 900,
                                  "package_tier_budget": "superior, ~80k"},
                        "codes_checklist": {"items": []}})
    quote_call = next(kw for n, kw in fake.calls if n == "quotes")
    assert quote_call["package_tier"] == "SUPERIOR"
    assert out["takeoff"]["lines"][0]["quantity"] == 945
    assert out["retrieved"]["past_project_quote"][0]["citation"] == "Past project P19"


def test_takeoff_node_passes_eval_exclusion_hook_when_present(monkeypatch):
    """_eval_exclude_project_codes is an eval-only state key (see
    run_quote_accuracy_eval.py) -- absent in every production path, so
    None flows through unchanged there; when present it must reach
    search_past_quotes so leave-one-out retrieval actually excludes."""
    fake = _patch_stage_retrievers(monkeypatch)
    takeoff_json = json.dumps({"gfa_sqft": 900, "lines": [], "assumptions": []})
    monkeypatch.setattr(nodes, "takeoff_model", lambda: _FakeStageModel(
        SimpleNamespace(content=takeoff_json)))
    takeoff_node({"slots": {"scope": "basement"}, "codes_checklist": {"items": []},
                 "_eval_exclude_project_codes": ["P19", "S01"]})
    quote_call = next(kw for n, kw in fake.calls if n == "quotes")
    assert quote_call["exclude_project_codes"] == ["P19", "S01"]

    fake2 = _patch_stage_retrievers(monkeypatch)
    monkeypatch.setattr(nodes, "takeoff_model", lambda: _FakeStageModel(
        SimpleNamespace(content=takeoff_json)))
    takeoff_node({"slots": {"scope": "basement"}, "codes_checklist": {"items": []}})
    quote_call2 = next(kw for n, kw in fake2.calls if n == "quotes")
    assert quote_call2["exclude_project_codes"] is None


def test_takeoff_node_degrades_to_none_on_unparseable_output(monkeypatch):
    _patch_stage_retrievers(monkeypatch)
    model = _FakeStageModel(SimpleNamespace(content="no json"),
                            SimpleNamespace(content="still no json"))
    monkeypatch.setattr(nodes, "takeoff_model", lambda: model)
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


def test_price_fill_lump_sum_trade_charged_once_across_multiple_lines(monkeypatch):
    """A lump-sum trade's rate already covers that trade's whole scope (see
    its "includes" column) -- if the takeoff splits that scope across
    several lines (observed live: electrical_rough_and_finish split 4 ways),
    only the first should charge the lump sum; the rest are the same job,
    not additional cost."""
    monkeypatch.setattr(nodes.settings, "tavily_api_key", "")
    monkeypatch.setattr(nodes.labor, "lookup", lambda t, b: _labor_row(
        trade="electrical_rough_and_finish", unit="lump_sum",
        rate_low_cad=6000, rate_high_cad=10000))
    out = price_fill_node(_takeoff_state(
        {"id": "t1", "category": "electrical", "trade": "electrical_rough_and_finish",
         "quantity": 1, "unit": "lump_sum"},
        {"id": "t2", "category": "life_safety", "trade": "electrical_rough_and_finish",
         "quantity": 3, "unit": "each"},
        {"id": "t3", "category": "life_safety", "trade": "electrical_rough_and_finish",
         "quantity": 1, "unit": "lump_sum"}))
    rows = out["price_resolution"]
    assert rows[0]["extended_quoted_cad"] == 8000.0  # first line charges the lump sum
    for dup in rows[1:]:
        assert dup["extended_quoted_cad"] == 0
        assert dup["extended_low_cad"] == 0 and dup["extended_high_cad"] == 0
        assert dup["price_source"] == "labor_rate"  # still traceable, not "unpriced"
        assert "already charged" in dup["note"]


def test_price_fill_lump_sum_dedup_is_per_trade_not_global(monkeypatch):
    monkeypatch.setattr(nodes.settings, "tavily_api_key", "")
    monkeypatch.setattr(nodes.labor, "lookup", lambda t, b: _labor_row(
        trade=t, unit="lump_sum", rate_low_cad=3000, rate_high_cad=6000))
    out = price_fill_node(_takeoff_state(
        {"id": "t1", "category": "electrical", "trade": "electrical_rough_and_finish",
         "quantity": 1, "unit": "lump_sum"},
        {"id": "t2", "category": "hvac", "trade": "hvac_rough_and_finish",
         "quantity": 1, "unit": "lump_sum"}))
    rows = out["price_resolution"]
    assert all(r["extended_quoted_cad"] == 4500.0 for r in rows), (
        "different lump-sum trades must each be charged independently")


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


def test_price_fill_same_item_and_allowance_key_prices_once_not_twice(monkeypatch):
    """Confirmed live across 22 real quotes / 43 lines: the takeoff routinely
    sets item == allowance_item for finish items (doors, baseboards, the
    egress window, etc.), and since the allowance table has no distinct $
    entry for those keys, both branches fell back to the identical
    material-sheet price -- silently double-charging 6-15% of every quote's
    total. Must collapse to exactly one row."""
    monkeypatch.setattr(nodes.settings, "tavily_api_key", "")
    monkeypatch.setattr(nodes.allowances, "lookup", lambda c, i: None)

    class _FakeMaterialRow:
        price_low_cad, price_high_cad, unit, source = 180.0, 320.0, "per_door_cad", "supplier list"
        updated_at = __import__("datetime").date(2026, 6, 15)
    monkeypatch.setattr(nodes.materials, "lookup", lambda c, i: _FakeMaterialRow())

    out = price_fill_node(_takeoff_state(
        {"id": "t9", "category": "doors", "item": "interior",
         "allowance_item": "interior", "quantity": 1, "unit": "each"},
        slots={"package_tier_budget": "essential, ~50k"}))
    rows = out["price_resolution"]
    assert len(rows) == 1
    assert rows[0]["extended_quoted_cad"] == 250.0


# --- draft total: computed in code, never left to the LLM's own summation ---

def test_total_contract_value_sums_priced_rows_and_lists_excluded():
    from app.agent.nodes import _total_contract_value
    rows = [
        {"description": "Flooring", "extended_quoted_cad": 100.5},
        {"description": "Drywall", "extended_quoted_cad": 200},
        {"description": "Floor drain", "price_source": "unpriced"},
        {"category": "kitchen", "price_source": "tavily"},  # no extended_quoted_cad
    ]
    out = _total_contract_value(rows)
    assert out["total_contract_value_cad"] == 300.5
    assert out["excluded_unpriced_lines"] == ["Floor drain", "kitchen"]


def test_total_contract_value_empty_price_resolution():
    from app.agent.nodes import _total_contract_value
    assert _total_contract_value([]) == {
        "total_contract_value_cad": 0, "excluded_unpriced_lines": []}


# --- generation time/cost tracking: real per-call usage, computed in code ---

def test_usage_entry_reads_real_response_fields():
    """Mirrors the actual shape confirmed live off a real OpenRouter/
    langchain_openai response: usage_metadata for tokens, response_metadata
    .token_usage.cost for OpenRouter's own billed cost."""
    from app.agent.nodes import _usage_entry
    resp = SimpleNamespace(
        usage_metadata={"input_tokens": 14, "output_tokens": 4, "total_tokens": 18},
        response_metadata={"model_name": "anthropic/claude-haiku-4.5",
                           "token_usage": {"cost": 3.4e-05}})
    assert _usage_entry("codes", resp) == {
        "stage": "codes", "model": "anthropic/claude-haiku-4.5",
        "input_tokens": 14, "output_tokens": 4, "cost_usd": 3.4e-05}


def test_usage_entry_degrades_for_bare_test_double():
    """A plain SimpleNamespace(content=...) fake (used throughout this test
    file) has neither attribute -- must degrade to zeros/None, never crash."""
    from app.agent.nodes import _usage_entry
    resp = SimpleNamespace(content="hello")
    assert _usage_entry("draft", resp) == {
        "stage": "draft", "model": "", "input_tokens": 0,
        "output_tokens": 0, "cost_usd": None}


def test_generation_summary_sums_and_flags_incomplete_cost():
    from app.agent.nodes import _generation_summary
    stats = [
        {"stage": "codes", "input_tokens": 100, "output_tokens": 20, "cost_usd": 0.001},
        {"stage": "draft", "input_tokens": 4000, "output_tokens": 1500, "cost_usd": 0.02},
        {"stage": "takeoff", "input_tokens": 200, "output_tokens": 50, "cost_usd": None},
    ]
    out = _generation_summary(stats)
    assert out["total_cost_usd"] == pytest.approx(0.021)
    assert out["cost_is_complete"] is False  # one entry had no cost data
    assert out["total_input_tokens"] == 4300
    assert out["total_output_tokens"] == 1570
    assert out["llm_calls"] == 3


def test_generation_summary_empty_list():
    from app.agent.nodes import _generation_summary
    assert _generation_summary([]) == {
        "total_cost_usd": 0, "cost_is_complete": True,
        "total_input_tokens": 0, "total_output_tokens": 0, "llm_calls": 0}


def test_graph_accumulates_generation_stats_across_stages(monkeypatch):
    """generation_stats is Annotated[list, operator.add] -- LangGraph must
    concatenate every node's own entries into one running list across the
    whole pipeline (codes -> takeoff -> verify -> draft), the same way
    `messages` already accumulates."""
    from langgraph.checkpoint.memory import InMemorySaver
    from app.agent.graph import build_graph

    def _resp(stage, content):
        return SimpleNamespace(content=content,
                               usage_metadata={"input_tokens": 100, "output_tokens": 10},
                               response_metadata={"model_name": f"fake-{stage}",
                                                  "token_usage": {"cost": 0.001}})

    _patch_stage_retrievers(monkeypatch)
    monkeypatch.setattr(nodes.settings, "tavily_api_key", "")
    monkeypatch.setattr(nodes, "codes_model",
                        lambda: _FakeStageModel(_resp("codes", _codes_json())))
    monkeypatch.setattr(nodes, "takeoff_model",
                        lambda: _FakeStageModel(_resp("takeoff", _GOOD_TAKEOFF)))
    monkeypatch.setattr(nodes, "takeoff_verifier_model",
                        lambda: _FakeStageModel(_resp("verify", _CLEAR)))
    monkeypatch.setattr(nodes, "drafting_model",
                        lambda: _FakeStageModel(_resp("draft", "# Quote")))

    g = build_graph(checkpointer=InMemorySaver())
    out = g.invoke({"estimator_feedback": "n/a", "slots": {"scope": "finished basement"}},
                   {"configurable": {"thread_id": "gen-stats"}})
    stages = [s["stage"] for s in out["generation_stats"]]
    assert stages == ["codes", "takeoff", "takeoff_verify", "draft"]
    assert all(s["cost_usd"] == 0.001 for s in out["generation_stats"])


def test_draft_node_passes_computed_total_to_prompt(monkeypatch):
    """The drafting LLM must receive the code-computed total verbatim --
    this is what makes the total reliably present instead of depending on
    the LLM to sum every line itself (seen live: intermittently omitted)."""
    _patch_stage_retrievers(monkeypatch)
    seen = {}

    class _FakeDrafter:
        def invoke(self, msgs):
            seen["user_msg"] = msgs[-1][1]
            return SimpleNamespace(content="# Quote")
    monkeypatch.setattr(nodes, "drafting_model", lambda: _FakeDrafter())

    from app.agent.nodes import draft_node
    draft_node({"slots": {}, "flags": [],
               "price_resolution": [{"description": "Flooring",
                                     "extended_quoted_cad": 500}]})
    assert "TOTAL CONTRACT VALUE" in seen["user_msg"]
    assert '"total_contract_value_cad": 500' in seen["user_msg"]


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
    monkeypatch.setattr(nodes, "takeoff_model", lambda: model)
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
    monkeypatch.setattr(nodes, "takeoff_model", lambda: model)
    checklist = {"items": [{"id": "c1", "requirement": "egress window",
                            "citation": "OBC 9.9.10.1", "doc_type": "building_code",
                            "action": "line_item"}]}
    out = takeoff_node({"slots": {"scope": "finished basement"},
                        "codes_checklist": checklist})
    assert len(out["takeoff"]["lines"]) == 1  # nothing injected — already covered
    assert out["takeoff"]["lines"][0]["id"] == "t1"  # ids assigned in code


# --- takeoff verifier: catches new-construction-vs-existing-scope and ------
# --- self-contradiction, with a bounded retry before neutralizing ----------

def test_verify_takeoff_node_parses_issues(monkeypatch):
    from app.agent.nodes import verify_takeoff_node
    model = _FakeStageModel(SimpleNamespace(
        content='{"issues": [{"line_id": "t1", "reason": "contradicts existing egress slot"}]}'))
    monkeypatch.setattr(nodes, "takeoff_verifier_model", lambda: model)
    out = verify_takeoff_node({"slots": {}, "takeoff": {"lines": [{"id": "t1"}]}})
    assert out["takeoff_issues"] == [{"line_id": "t1", "reason": "contradicts existing egress slot",
                                      "type": "contradiction"}]
    assert out["takeoff_verify_attempts"] == 1


def test_verify_takeoff_node_degrades_on_unparseable_output(monkeypatch):
    from app.agent.nodes import verify_takeoff_node
    model = _FakeStageModel(SimpleNamespace(content="not json at all"))
    monkeypatch.setattr(nodes, "takeoff_verifier_model", lambda: model)
    out = verify_takeoff_node({"slots": {}, "takeoff": {"lines": [{"id": "t1"}]}})
    assert out["takeoff_issues"] == [] and out["takeoff_verify_attempts"] == 1
    assert len(out["generation_stats"]) == 1  # the call still happened, still costs money


def test_verify_takeoff_node_skips_llm_when_no_takeoff(monkeypatch):
    from app.agent.nodes import verify_takeoff_node

    def _explode():
        raise AssertionError("must not call the verifier with no takeoff to check")
    monkeypatch.setattr(nodes, "takeoff_verifier_model", _explode)
    out = verify_takeoff_node({"slots": {}, "takeoff": None, "takeoff_verify_attempts": 2})
    assert out == {"takeoff_issues": [], "takeoff_verify_attempts": 3, "generation_stats": []}


def test_verify_takeoff_node_parses_omission_issue(monkeypatch):
    """A filled slot with no corresponding takeoff line at all -- confirmed
    live: bathroom_rough_in filled, zero bathroom lines generated, ~$20k of
    scope silently dropped with nothing flagging it."""
    from app.agent.nodes import verify_takeoff_node
    model = _FakeStageModel(SimpleNamespace(
        content='{"issues": [{"type": "omission", "slot": "bathroom_rough_in", '
                '"reason": "slot filled but no bathroom line exists"}]}'))
    monkeypatch.setattr(nodes, "takeoff_verifier_model", lambda: model)
    out = verify_takeoff_node({"slots": {"bathroom_rough_in": "existing rough-in"},
                               "takeoff": {"lines": [{"id": "t1"}]}})
    assert out["takeoff_issues"] == [{"type": "omission", "slot": "bathroom_rough_in",
                                      "reason": "slot filled but no bathroom line exists"}]


def test_verify_takeoff_node_drops_malformed_typed_issues(monkeypatch):
    """An "omission" with no slot, or a "contradiction" with no line_id, has
    nothing to act on downstream -- drop it rather than let it silently
    become a no-op flag or crash the price_fill lookup."""
    from app.agent.nodes import verify_takeoff_node
    model = _FakeStageModel(SimpleNamespace(
        content='{"issues": ['
                '{"type": "omission", "reason": "no slot given"}, '
                '{"type": "contradiction", "reason": "no line_id given"}]}'))
    monkeypatch.setattr(nodes, "takeoff_verifier_model", lambda: model)
    out = verify_takeoff_node({"slots": {}, "takeoff": {"lines": [{"id": "t1"}]}})
    assert out["takeoff_issues"] == []


_BAD_TAKEOFF = json.dumps({"gfa_sqft": 900, "lines": [
    {"category": "windows", "item": "egress_window", "quantity": 1, "unit": "each",
     "description": "New egress window + concrete cutting for window well"}],
    "assumptions": []})
_GOOD_TAKEOFF = json.dumps({"gfa_sqft": 900, "lines": [
    {"category": "windows", "item": "egress_window", "quantity": 1, "unit": "each",
     "description": "Egress window, existing, verify compliance only"}],
    "assumptions": []})
_FLAGGED = '{"issues": [{"line_id": "t1", "reason": "prices new construction for a window intake marks as existing"}]}'
_CLEAR = '{"issues": []}'


def _mock_pipeline_models(monkeypatch, takeoff_responses, verify_responses):
    # Each *_model() call is a factory in the real code (a fresh ChatOpenAI
    # instance every time) -- but the fake here must be the SAME stateful
    # instance across calls, or every retry would see the response list
    # reset back to the first entry instead of advancing.
    _patch_stage_retrievers(monkeypatch)
    monkeypatch.setattr(nodes.settings, "tavily_api_key", "")
    codes_fake = _FakeStageModel(SimpleNamespace(content=_codes_json(), tool_calls=[]))
    takeoff_fake = _FakeStageModel(*[SimpleNamespace(content=r) for r in takeoff_responses])
    verify_fake = _FakeStageModel(*[SimpleNamespace(content=r) for r in verify_responses])
    draft_fake = _FakeStageModel(SimpleNamespace(content="# Quote"))
    monkeypatch.setattr(nodes, "codes_model", lambda: codes_fake)
    monkeypatch.setattr(nodes, "takeoff_model", lambda: takeoff_fake)
    monkeypatch.setattr(nodes, "takeoff_verifier_model", lambda: verify_fake)
    monkeypatch.setattr(nodes, "drafting_model", lambda: draft_fake)

    class _FakeMaterialRow:
        price_low_cad, price_high_cad, unit, source = 3500.0, 6500.0, "per_opening_cad", "sub-trade quote"
        updated_at = __import__("datetime").date.today()
    monkeypatch.setattr(nodes.materials, "lookup", lambda c, i: _FakeMaterialRow())
    monkeypatch.setattr(nodes.allowances, "lookup", lambda c, i: None)


def test_graph_takeoff_retry_resolves_on_second_attempt(monkeypatch):
    """Verifier flags the first takeoff, the retry produces a corrected one,
    verifier clears it -- prices normally, no neutralization, exactly one
    retry (not looping again once resolved)."""
    from langgraph.checkpoint.memory import InMemorySaver
    from app.agent.graph import build_graph
    _mock_pipeline_models(monkeypatch,
                         takeoff_responses=[_BAD_TAKEOFF, _GOOD_TAKEOFF],
                         verify_responses=[_FLAGGED, _CLEAR])
    g = build_graph(checkpointer=InMemorySaver())
    out = g.invoke({"estimator_feedback": "n/a", "slots": {"scope": "finished basement"}},
                   {"configurable": {"thread_id": "retry-ok"}})
    assert out["takeoff_issues"] == []
    assert out["takeoff_verify_attempts"] == 2
    row = out["price_resolution"][0]
    assert row["price_source"] == "price_sheet"  # priced normally, not neutralized
    assert row["extended_quoted_cad"]


def test_graph_takeoff_retry_exhausted_neutralizes_flagged_line(monkeypatch):
    """Verifier keeps flagging the same line past the retry cap -- takeoff
    is attempted exactly twice (the _FakeStageModel raises on a 3rd .invoke()
    with no responses left, so an extra retry would fail this test), and the
    still-flagged line is neutralized instead of reaching the total priced."""
    from langgraph.checkpoint.memory import InMemorySaver
    from app.agent.graph import build_graph
    _mock_pipeline_models(monkeypatch,
                         takeoff_responses=[_BAD_TAKEOFF, _BAD_TAKEOFF],
                         verify_responses=[_FLAGGED, _FLAGGED])
    g = build_graph(checkpointer=InMemorySaver())
    out = g.invoke({"estimator_feedback": "n/a", "slots": {"scope": "finished basement"}},
                   {"configurable": {"thread_id": "retry-exhausted"}})
    assert out["takeoff_verify_attempts"] == 2
    row = out["price_resolution"][0]
    assert row["price_source"] == "unpriced"
    assert row["extended_quoted_cad"] is None
    assert "verifier flagged" in row["note"]


def test_price_fill_injects_placeholder_for_unresolved_omission(monkeypatch):
    """No line exists for an omission issue -- there's nothing to neutralize
    by ref, so a new synthetic unpriced row must be injected instead (same
    precedent as _enforce_code_coverage's injected line for a dropped
    mandatory code item)."""
    monkeypatch.setattr(nodes.settings, "tavily_api_key", "")
    out = price_fill_node({
        "takeoff": {"lines": [{"id": "t1", "category": "flooring", "item": "lvp",
                              "quantity": 100, "unit": "sqft"}]},
        "takeoff_issues": [{"type": "omission", "slot": "bathroom_rough_in",
                           "reason": "slot filled but no bathroom line exists"}]})
    rows = out["price_resolution"]
    assert len(rows) == 2  # the real t1 row, untouched, plus the injected one
    assert rows[0]["takeoff_line_ref"] == "t1" and rows[0]["price_source"] == "price_sheet"
    placeholder = rows[1]
    assert placeholder["price_source"] == "unpriced"
    assert placeholder["takeoff_line_ref"] == "omission-bathroom_rough_in"
    assert "bathroom_rough_in" in placeholder["description"]
    assert "no bathroom line exists" in placeholder["note"]


def test_graph_takeoff_omission_neutralization(monkeypatch):
    """Full pipeline: an omission issue survives to price_fill and produces
    an injected placeholder line, without disturbing the real priced lines."""
    from langgraph.checkpoint.memory import InMemorySaver
    from app.agent.graph import build_graph
    omission_issue = ('{"issues": [{"type": "omission", "slot": "bathroom_rough_in", '
                      '"reason": "slot filled but no bathroom line exists"}]}')
    _mock_pipeline_models(monkeypatch,
                         takeoff_responses=[_GOOD_TAKEOFF, _GOOD_TAKEOFF],
                         verify_responses=[omission_issue, omission_issue])
    g = build_graph(checkpointer=InMemorySaver())
    out = g.invoke({"estimator_feedback": "n/a",
                    "slots": {"scope": "finished basement", "bathroom_rough_in": "existing rough-in"}},
                   {"configurable": {"thread_id": "omission"}})
    rows = out["price_resolution"]
    assert any(r.get("takeoff_line_ref") == "omission-bathroom_rough_in"
              and r["price_source"] == "unpriced" for r in rows)
    real_row = next(r for r in rows if r.get("takeoff_line_ref") == "t1")
    assert real_row["price_source"] == "price_sheet"  # untouched by the omission handling


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
    monkeypatch.setattr(nodes, "takeoff_model", lambda: _FakeDrafter())
    monkeypatch.setattr(nodes, "drafting_model", lambda: _FakeDrafter())
    monkeypatch.setattr(nodes, "takeoff_verifier_model",
                        lambda: SimpleNamespace(invoke=lambda msgs: SimpleNamespace(
                            content='{"issues": []}')))

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
