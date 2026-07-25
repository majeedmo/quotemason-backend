"""Typed loaders for the Task 5 evaluation datasets.

Three components (docs/submission.md Task 5):

- ``ragas_testset.jsonl`` — RAGAS-generated synthetic QA pairs (breadth over the
  whole corpus; frozen output of ``app.evals.generate_testset``, committed so
  every run — including the Task 6 retriever comparison — scores an identical set).
- ``retrieval_golden.jsonl`` — hand-anchored retrieval cases whose ground truth
  is expressed against chunk metadata (``section_number`` / ``project_code``),
  so hit-rate survives re-chunking and retriever swaps.
- ``agent_scenarios.json`` — scripted end-to-end intake conversations with
  expected routing and judge criteria for the LLM-judge half of the harness.

Loaders are stdlib-only: neither the test suite nor the runtime needs ragas
installed (it is imported only by the one-time generation script).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"

RAGAS_TESTSET_PATH = DATA_DIR / "ragas_testset.jsonl"
RETRIEVAL_GOLDEN_PATH = DATA_DIR / "retrieval_golden.jsonl"
AGENT_SCENARIOS_PATH = DATA_DIR / "agent_scenarios.json"
QUOTE_ACCURACY_CASES_PATH = DATA_DIR / "quote_accuracy_cases.json"

ROUTES = ("draft", "ask", "hard_route")


def _match(matcher: dict, metadata: dict) -> bool:
    """True when every key in the matcher holds against the chunk metadata.

    ``section_number`` matches exactly or by child prefix ("9.9.10" covers
    "9.9.10.1"); ``section_title_contains`` is a case-insensitive substring;
    anything else is string equality.
    """
    for key, want in matcher.items():
        if key == "section_number":
            got = str(metadata.get("section_number", ""))
            if not (got == want or got.startswith(f"{want}.")):
                return False
        elif key == "section_title_contains":
            if str(want).lower() not in str(metadata.get("section_title", "")).lower():
                return False
        elif str(metadata.get(key, "")) != str(want):
            return False
    return True


@dataclass
class SyntheticCase:
    """One RAGAS-generated QA row (native field names, ready for ragas.evaluate)."""

    user_input: str
    reference_contexts: list[str]
    reference: str
    synthesizer_name: str = ""


@dataclass
class RetrievalCase:
    """One hand-anchored retrieval case.

    ``ground_truth``/``forbidden`` are lists of matchers (OR-ed); each matcher is
    a dict of metadata constraints (AND-ed) — see ``_match``. ``filters`` are the
    kwargs the harness passes to the doc_type's retriever helper.
    """

    id: str
    question: str
    doc_type: str
    ground_truth: list[dict]
    reference_answer: str
    filters: dict = field(default_factory=dict)
    forbidden: list[dict] = field(default_factory=list)
    notes: str = ""

    def matches(self, metadata: dict) -> bool:
        return any(_match(m, metadata) for m in self.ground_truth)

    def violates(self, metadata: dict) -> bool:
        return any(_match(m, metadata) for m in self.forbidden)


@dataclass
class AgentScenario:
    """One scripted end-to-end conversation for the judge harness.

    ``turns`` are customer messages sent in order on a single thread_id.
    ``expected_flag_texts`` are substrings that must appear in the draft's flag
    block (guideline §6.2 flag texts). A scenario with empty ``turns`` and a
    non-empty ``applies_to`` is a cross-cutting judge rubric over other
    scenarios' drafts (q7 citation quality).
    """

    id: str
    title: str
    turns: list[str]
    expected_route: str
    expected_flag_texts: list[str] = field(default_factory=list)
    judge_criteria: list[str] = field(default_factory=list)
    references: dict = field(default_factory=dict)
    applies_to: list[str] = field(default_factory=list)


@dataclass
class QuoteAccuracyCase:
    """One real past project used as a quote-accuracy test case: reconstructed
    intake slots (hand-authored from a careful reading of the quote body —
    frontmatter alone only carries scope/gfa_sqft/tier/city) run through
    codes -> takeoff -> price_fill -> draft directly (see
    run_quote_accuracy_eval.run_pipeline), skipping intake.

    ``exclude_project_codes`` is this project's own code plus its synthetic
    twin where one exists (see corpus frontmatter ``paired_with``) — leave-
    one-out so takeoff_node's comparable-project retrieval can't hand the
    pipeline its own historical answer.
    """

    id: str
    project_code: str
    slots: dict
    actual_total_cad: float
    exclude_project_codes: list[str]
    tolerance_pct: float = 5.0
    notes: str = ""


def load_ragas_testset(path: Path = RAGAS_TESTSET_PATH) -> list[SyntheticCase]:
    cases = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        cases.append(SyntheticCase(
            user_input=row["user_input"],
            reference_contexts=list(row.get("reference_contexts") or []),
            reference=row["reference"],
            synthesizer_name=row.get("synthesizer_name", ""),
        ))
    return cases


def load_retrieval_golden(path: Path = RETRIEVAL_GOLDEN_PATH) -> list[RetrievalCase]:
    return [RetrievalCase(**json.loads(line))
            for line in path.read_text().splitlines() if line.strip()]


def load_agent_scenarios(path: Path = AGENT_SCENARIOS_PATH) -> list[AgentScenario]:
    return [AgentScenario(**row) for row in json.loads(path.read_text())]


def load_quote_accuracy_cases(path: Path = QUOTE_ACCURACY_CASES_PATH) -> list[QuoteAccuracyCase]:
    return [QuoteAccuracyCase(**row) for row in json.loads(path.read_text())]
