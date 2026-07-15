"""No-network tests for the Task 5 eval-harness scoring logic."""

import json
from types import SimpleNamespace

import pytest

from app.evals.dataset import RetrievalCase, load_retrieval_golden
from app.evals.judge import parse_verdicts
from app.evals.run_retrieval_eval import _search, score_case
from app.evals.run_scenario_eval import (
    check_flags,
    customer_visible_dollars,
    derive_route,
)


def _chunk(**meta):
    return SimpleNamespace(text="x", score=0.9, metadata=meta)


def _case(**overrides):
    base = dict(id="t", question="q", doc_type="building_code",
                ground_truth=[{"section_number": "9.9.10"}],
                reference_answer="a", filters={}, forbidden=[])
    base.update(overrides)
    return RetrievalCase(**base)


# --- score_case ---------------------------------------------------------

def test_score_case_prefix_hit_and_mrr():
    chunks = [_chunk(section_number="9.5.3.1", doc_type="building_code"),
              _chunk(section_number="9.9.10.1", doc_type="building_code")]
    r = score_case(_case(), chunks)
    assert r.hit and r.rank == 2 and r.reciprocal_rank == 0.5


def test_score_case_miss():
    r = score_case(_case(), [_chunk(section_number="9.8.4.1")])
    assert not r.hit and r.rank is None and r.reciprocal_rank == 0.0


def test_score_case_forbidden_violation():
    case = _case(doc_type="past_project_quote",
                 ground_truth=[{"project_code": "P19"}],
                 forbidden=[{"project_code": "S01"}])
    chunks = [_chunk(project_code="S01", synthetic=True),
              _chunk(project_code="P19")]
    r = score_case(case, chunks)
    assert r.hit and r.rank == 2 and r.violations == ["S01"]


# --- doc_type dispatch ---------------------------------------------------

class _Recorder:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def _record(query, **kwargs):
            self.calls.append((name, kwargs))
            return []
        return _record


def test_dispatch_passes_filters():
    rec = _Recorder()
    _search(rec, _case(doc_type="past_project_quote",
                       filters={"city": "Oakville", "include_synthetic": False},
                       ground_truth=[{"project_code": "P19"}]), k=5)
    name, kwargs = rec.calls[0]
    assert name == "search_past_quotes"
    assert kwargs["city"] == "Oakville" and kwargs["include_synthetic"] is False


def test_dispatch_all_golden_cases_resolve():
    rec = _Recorder()
    for case in load_retrieval_golden():
        _search(rec, case, k=5)
    assert len(rec.calls) == len(load_retrieval_golden())


# --- judge parsing -------------------------------------------------------

def test_parse_verdicts_plain_and_fenced():
    obj = {"criteria": [{"criterion": "c", "verdict": "pass", "evidence": "e"}],
           "summary": "s"}
    assert parse_verdicts(json.dumps(obj))["criteria"][0]["verdict"] == "pass"
    fenced = f"```json\n{json.dumps(obj)}\n```"
    assert parse_verdicts(fenced)["summary"] == "s"


def test_parse_verdicts_prose_wrapped():
    obj = {"criteria": [], "summary": "ok"}
    raw = f"Here is my assessment.\n{json.dumps(obj)}\nDone."
    assert parse_verdicts(raw)["summary"] == "ok"


def test_parse_verdicts_garbage_degrades():
    out = parse_verdicts("no json here at all")
    assert out["error"] and out["criteria"] == []


# --- scenario deterministic checks ---------------------------------------

def test_derive_route():
    assert derive_route({"routing_packet": {"route": "hard"}}) == "hard_route"
    assert derive_route({"draft": "# quote"}) == "draft"
    assert derive_route({"messages": []}) == "ask"


def test_check_flags_matches_flag_block_or_draft():
    state = {"flags": [{"flag_text": "Pricing confidence LOW — no comparable"}],
             "draft": "…"}
    assert check_flags(["Pricing confidence LOW"], state)["ok"]
    assert not check_flags(["200 A upgrade assumed"], state)["ok"]
    assert check_flags([], {"draft": None})["ok"]


@pytest.mark.parametrize("text,expected", [
    ("the estimate is $55k", True),
    ("total: $ 1,200", True),
    ("a 200 amp panel and 900 sqft", False),
    ("we'll be in touch shortly", False),
])
def test_customer_visible_dollars(text, expected):
    assert customer_visible_dollars(text) is expected
