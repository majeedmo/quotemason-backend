"""Validation for the Task 5 eval datasets — no network, no API keys.

The load-bearing test is ground-truth existence: every hand-anchored matcher
must resolve against the real chunker output over the real corpus, so the
golden set can never drift from what ingestion actually produces.
"""

from pathlib import Path

import pytest

from app.agent import guidelines
from app.evals.dataset import (
    RAGAS_TESTSET_PATH,
    ROUTES,
    load_agent_scenarios,
    load_quote_accuracy_cases,
    load_ragas_testset,
    load_retrieval_golden,
)
from app.config import CORPUS_DIR
from app.ingestion.chunking import chunk_doc
from app.ingestion.loaders import load_all

DOC_TYPES = {"building_code", "zoning_bylaw", "builder_guideline", "past_project_quote"}
FILTER_KEYS = {"city", "package_tier", "scope", "include_synthetic", "jurisdiction"}


@pytest.fixture(scope="module")
def chunk_metas():
    return [chunk.metadata for doc in load_all() for chunk in chunk_doc(doc)]


@pytest.fixture(scope="module")
def golden():
    return load_retrieval_golden()


@pytest.fixture(scope="module")
def scenarios():
    return load_agent_scenarios()


@pytest.fixture(scope="module")
def quote_accuracy_cases():
    return load_quote_accuracy_cases()


def test_golden_schema(golden):
    assert len(golden) >= 20
    ids = [c.id for c in golden]
    assert len(ids) == len(set(ids)), "duplicate case ids"
    for case in golden:
        assert case.doc_type in DOC_TYPES, case.id
        assert case.question and case.reference_answer, case.id
        assert case.ground_truth, case.id
        assert set(case.filters) <= FILTER_KEYS, case.id


def test_golden_covers_all_doc_types(golden):
    assert {c.doc_type for c in golden} == DOC_TYPES


def test_ground_truth_exists_in_corpus(golden, chunk_metas):
    for case in golden:
        of_type = [m for m in chunk_metas if m.get("doc_type") == case.doc_type]
        assert any(case.matches(m) for m in of_type), (
            f"{case.id}: no chunk satisfies any ground-truth matcher {case.ground_truth}"
        )
        # A forbidden matcher that matches nothing would make the exclusion vacuous.
        for matcher in case.forbidden:
            assert any(case.violates(m) for m in of_type), (
                f"{case.id}: forbidden matcher {matcher} matches no chunk"
            )


def test_section_number_prefix_matching(golden, chunk_metas):
    case = next(c for c in golden if c.id == "bc-egress-window")
    matched = {m["section_number"] for m in chunk_metas if case.matches(m)}
    assert any(s.startswith("9.9.10") for s in matched)


def test_scenarios_schema(scenarios):
    assert len(scenarios) == 7, "one scenario per submission §1.4 eval question"
    ids = [s.id for s in scenarios]
    assert len(ids) == len(set(ids))
    for s in scenarios:
        assert s.expected_route in ROUTES, s.id
        assert s.judge_criteria, s.id
        if s.applies_to:
            assert not s.turns, f"{s.id}: cross-cutting rubric must have no turns"
            assert set(s.applies_to) <= set(ids), s.id
        else:
            assert s.turns, f"{s.id}: conversational scenario needs turns"


def test_q6_trips_deterministic_hard_scan(scenarios):
    q6 = next(s for s in scenarios if s.expected_route == "hard_route")
    hits = guidelines.scan_hard_triggers(" ".join(q6.turns))
    assert hits, "q6 turns must trip the §6.1 keyword scan deterministically"


def test_non_hard_scenarios_do_not_trip_hard_scan(scenarios):
    for s in scenarios:
        if s.expected_route != "hard_route" and s.turns:
            assert guidelines.scan_hard_triggers(" ".join(s.turns)) == [], (
                f"{s.id} unexpectedly trips a §6.1 hard trigger"
            )


def test_q4_references_answer_key_outside_corpus(scenarios):
    q4 = next(s for s in scenarios if "tier" in s.id)
    key = q4.references.get("answer_key", "")
    assert key.startswith("docs/"), "answer key must live outside corpus/"


def _quote_file(project_code: str) -> Path:
    matches = list((CORPUS_DIR / "quotes-redacted").glob(f"{project_code}-*.md"))
    matches += list((CORPUS_DIR / "quotes-synthetic").glob(f"{project_code}-*.md"))
    assert len(matches) == 1, f"expected exactly one corpus file for {project_code}, found {matches}"
    return matches[0]


def test_quote_accuracy_cases_schema(quote_accuracy_cases):
    assert len(quote_accuracy_cases) == 6
    ids = [c.id for c in quote_accuracy_cases]
    assert len(ids) == len(set(ids)), "duplicate case ids"
    for c in quote_accuracy_cases:
        assert c.project_code in c.exclude_project_codes, (
            f"{c.id}: leave-one-out exclusion must include the case's own project_code"
        )
        assert c.slots, c.id
        assert c.actual_total_cad > 0, c.id


def test_quote_accuracy_project_codes_exist_in_corpus(quote_accuracy_cases):
    for c in quote_accuracy_cases:
        _quote_file(c.project_code)  # raises if missing/ambiguous
        for excluded in c.exclude_project_codes:
            _quote_file(excluded)


def test_quote_accuracy_actual_total_matches_corpus_text(quote_accuracy_cases):
    """Guards against a transcription typo in actual_total_cad: the exact
    dollar figure (as it appears in the quote's own TOTAL line, e.g.
    "$119,999.00") must appear verbatim somewhere in that project's corpus
    file text."""
    for c in quote_accuracy_cases:
        text = _quote_file(c.project_code).read_text()
        formatted = f"{c.actual_total_cad:,.2f}"
        assert formatted in text, (
            f"{c.id}: ${formatted} not found in {c.project_code}'s corpus file"
        )


def test_ragas_testset_rows():
    if not RAGAS_TESTSET_PATH.exists():
        pytest.skip("frozen testset not generated yet — run: uv run --group evals "
                    "python -m app.evals.generate_testset")
    cases = load_ragas_testset()
    assert len(cases) >= 20
    for c in cases:
        assert c.user_input.strip()
        assert c.reference.strip()
        assert c.reference_contexts
