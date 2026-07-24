"""No-network tests for the quote-accuracy eval harness's scoring logic.

Pipeline nodes are monkeypatched (SimpleNamespace-style fakes, same pattern
as test_agent.py) so these tests exercise run_pipeline/run_case's own logic
-- priced/unpriced split, coverage math, pct_error/within_tolerance --
without any LLM or network call.
"""

import pytest

import app.evals.run_quote_accuracy_eval as qa
from app.evals.dataset import QuoteAccuracyCase
from app.evals.run_quote_accuracy_eval import (pct_error, run_case,
                                               run_pipeline, within_tolerance)


# --- pure scoring functions ---------------------------------------------

def test_pct_error_basic():
    assert pct_error(105, 100) == 5.0
    assert pct_error(95, 100) == -5.0
    assert pct_error(100, 100) == 0.0


def test_pct_error_actual_zero_edge_case():
    assert pct_error(0, 0) == 0.0
    assert pct_error(50, 0) == float("inf")


def test_within_tolerance():
    assert within_tolerance(4.9, 5.0)
    assert within_tolerance(-5.0, 5.0)  # boundary is inclusive
    assert not within_tolerance(5.1, 5.0)


# --- run_pipeline: merges each node's dict into a running state ---------

def test_run_pipeline_merges_node_outputs_in_order(monkeypatch):
    calls = []

    def fake_codes(state):
        calls.append(("codes", dict(state)))
        return {"codes_checklist": {"items": []}}

    def fake_takeoff(state):
        calls.append(("takeoff", dict(state)))
        assert state["codes_checklist"] == {"items": []}  # sees prior node's output
        return {"takeoff": {"lines": [], "gfa_sqft": 900}}

    def fake_price_fill(state):
        calls.append(("price_fill", dict(state)))
        return {"price_resolution": []}

    def fake_draft(state):
        calls.append(("draft", dict(state)))
        return {"draft": "# Quote"}

    monkeypatch.setattr(qa, "codes_node", fake_codes)
    monkeypatch.setattr(qa, "takeoff_node", fake_takeoff)
    monkeypatch.setattr(qa, "price_fill_node", fake_price_fill)
    monkeypatch.setattr(qa, "draft_node", fake_draft)

    final = run_pipeline({"slots": {"scope": "basement"}})
    assert [n for n, _ in calls] == ["codes", "takeoff", "price_fill", "draft"]
    assert final["draft"] == "# Quote"
    assert final["takeoff"]["gfa_sqft"] == 900


# --- run_case: priced/unpriced split, coverage, line breakdown ----------

def _case(**overrides):
    base = dict(id="qa-test", project_code="PXX", slots={"scope": "basement"},
               actual_total_cad=1000.0, exclude_project_codes=["PXX"])
    base.update(overrides)
    return QuoteAccuracyCase(**base)


def _stub_pipeline(monkeypatch, price_resolution, draft="# Quote"):
    monkeypatch.setattr(qa, "run_pipeline", lambda initial_state: {
        "price_resolution": price_resolution, "draft": draft,
        "codes_checklist": {"items": []}, "takeoff": {"lines": []}})


def test_run_case_computes_pct_error_from_priced_rows_only(monkeypatch):
    _stub_pipeline(monkeypatch, [
        {"price_source": "price_sheet", "extended_quoted_cad": 600.0},
        {"price_source": "labor_rate", "extended_quoted_cad": 400.0},
        {"price_source": "unpriced", "description": "custom millwork"},
    ])
    out = run_case(_case(actual_total_cad=1000.0))
    assert out["computed_total_cad"] == 1000.0  # unpriced row excluded from sum
    assert out["pct_error"] == 0.0
    assert out["within_tolerance"] is True
    assert out["coverage_pct"] == pytest.approx(66.7, abs=0.1)
    assert out["unpriced_lines"] == ["custom millwork"]


def test_run_case_outside_tolerance(monkeypatch):
    _stub_pipeline(monkeypatch, [
        {"price_source": "price_sheet", "extended_quoted_cad": 1200.0}])
    out = run_case(_case(actual_total_cad=1000.0, tolerance_pct=5.0))
    assert out["pct_error"] == 20.0
    assert out["within_tolerance"] is False


def test_run_case_line_breakdown_sorted_descending_by_extended_dollar(monkeypatch):
    _stub_pipeline(monkeypatch, [
        {"price_source": "price_sheet", "extended_quoted_cad": 50.0,
         "category": "paint", "takeoff_line_ref": "t1"},
        {"price_source": "labor_rate", "extended_quoted_cad": 500.0,
         "category": "framing", "takeoff_line_ref": "t2"},
        {"price_source": "allowance", "extended_quoted_cad": 200.0,
         "category": "kitchen", "takeoff_line_ref": "t3"},
    ])
    out = run_case(_case())
    cats = [r["category"] for r in out["line_breakdown"]]
    assert cats == ["framing", "kitchen", "paint"]


def test_run_case_empty_price_resolution_yields_zero_coverage(monkeypatch):
    _stub_pipeline(monkeypatch, [])
    out = run_case(_case(actual_total_cad=1000.0))
    assert out["computed_total_cad"] == 0.0
    assert out["coverage_pct"] == 0.0
    assert out["pct_error"] == -100.0


def test_run_case_passes_slots_and_exclusion_to_initial_state(monkeypatch):
    seen = {}

    def fake_run_pipeline(initial_state):
        seen.update(initial_state)
        return {"price_resolution": [], "draft": None,
               "codes_checklist": None, "takeoff": None}
    monkeypatch.setattr(qa, "run_pipeline", fake_run_pipeline)

    run_case(_case(slots={"scope": "finished basement", "gfa_sqft": 900},
                   exclude_project_codes=["P19", "S01"]))
    assert seen["slots"] == {"scope": "finished basement", "gfa_sqft": 900}
    assert seen["_eval_exclude_project_codes"] == ["P19", "S01"]
