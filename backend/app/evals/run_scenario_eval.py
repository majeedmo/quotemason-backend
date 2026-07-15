"""End-to-end agent scenario eval (Task 5 harness, judge half).

Runs each scripted scenario through the real LangGraph agent (live LLMs +
retrieval, in-memory checkpointer), applies deterministic checks in code, then
has the cross-family judge (gpt-5.1) score the rubric criteria. The q7
citation-quality scenario is cross-cutting: it re-judges the drafts produced by
the scenarios it ``applies_to``.

Live-API script:

    cd backend && uv run python -m app.evals.run_scenario_eval \
        [--only q2-vague] [--no-judge] [--json out.json]
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from app.evals.dataset import AgentScenario, load_agent_scenarios

NUDGE = ("That's everything I know — please proceed with reasonable assumptions "
         "for anything I couldn't answer.")
MAX_NUDGES = 2
DOLLAR = re.compile(r"\$\s*\d")

REPO_DIR = Path(__file__).resolve().parents[3]


def derive_route(state: dict) -> str:
    packet = state.get("routing_packet") or {}
    if packet.get("route") == "hard":
        return "hard_route"
    if state.get("draft"):
        return "draft"
    return "ask"


def _flag_present(text: str, haystack: str) -> bool:
    """A flag matches on contiguous substring, or on its words appearing in
    order with short gaps — so 'Pricing confidence LOW' matches
    'pricing confidence for the accessory-unit scope is LOW'."""
    haystack = haystack.lower()
    if text.lower() in haystack:
        return True
    tokens = re.findall(r"\w+", text.lower())
    if not tokens:
        return False
    pattern = r"\b" + r"\b.{0,60}?\b".join(re.escape(tok) for tok in tokens) + r"\b"
    return re.search(pattern, haystack, re.DOTALL) is not None


def check_flags(expected_texts: list[str], state: dict) -> dict:
    """Every expected flag text must appear in the flags or the draft body."""
    haystack = json.dumps(state.get("flags") or []) + "\n" + (state.get("draft") or "")
    found = {t: _flag_present(t, haystack) for t in expected_texts}
    return {"ok": all(found.values()), "found": found}


def customer_visible_dollars(transcript: str) -> bool:
    return bool(DOLLAR.search(transcript))


def run_scenario(graph, scenario: AgentScenario) -> dict:
    """Feed scripted turns (plus bounded nudges) and collect the outcome."""
    from langchain_core.messages import HumanMessage

    cfg = {"configurable": {"thread_id": f"eval-{scenario.id}"}}
    transcript_lines: list[str] = []
    state: dict = {}
    extra_turns = 0

    def _send(text: str) -> None:
        nonlocal state
        transcript_lines.append(f"CUSTOMER: {text}")
        state = graph.invoke({"messages": [HumanMessage(content=text)]}, cfg)
        reply = state["messages"][-1].content if state.get("messages") else ""
        transcript_lines.append(f"ASSISTANT: {reply}")

    for turn in scenario.turns:
        _send(turn)
    while state.get("_action") == "ask" and extra_turns < MAX_NUDGES:
        extra_turns += 1
        _send(NUDGE)

    return {
        "route": derive_route(state),
        "extra_turns": extra_turns,
        "flags": state.get("flags") or [],
        "draft": state.get("draft"),
        "routing_packet": state.get("routing_packet"),
        "transcript": "\n\n".join(transcript_lines),
    }


def evaluate_outcome(scenario: AgentScenario, outcome: dict) -> dict:
    """Deterministic (non-LLM) checks."""
    checks = {
        "route_ok": outcome["route"] == scenario.expected_route,
        "expected_route": scenario.expected_route,
        "actual_route": outcome["route"],
        "flags": check_flags(scenario.expected_flag_texts, outcome),
        "extra_turns": outcome["extra_turns"],
    }
    if scenario.expected_route == "hard_route":
        checks["no_dollar_figures"] = not customer_visible_dollars(outcome["transcript"])
        checks["routing_packet_present"] = bool(outcome.get("routing_packet"))
    return checks


def _answer_key(scenario: AgentScenario) -> str | None:
    rel = scenario.references.get("answer_key")
    return (REPO_DIR / rel).read_text() if rel else None


def run(only: str | None = None, use_judge: bool = True) -> dict:
    from langgraph.checkpoint.memory import InMemorySaver

    from app.agent.graph import build_graph
    from app.evals.judge import judge_model, score_scenario

    graph = build_graph(checkpointer=InMemorySaver())
    scenarios = load_agent_scenarios()
    if only:
        # Keep the selected scenario, plus the cross-cutting integrity both ways:
        # selecting a cross-cutting scenario (e.g. q7) pulls in the drafts it
        # judges (its applies_to), and selecting a draft pulls in the
        # cross-cutting scenario that judges it (and that one's other targets).
        keep = {only}
        for s in scenarios:
            if s.id == only:
                keep.update(s.applies_to)
            if only in s.applies_to:
                keep.add(s.id)
                keep.update(s.applies_to)
        scenarios = [s for s in scenarios if s.id in keep]

    llm = judge_model() if use_judge else None
    outcomes: dict[str, dict] = {}
    results: dict[str, dict] = {}

    for s in scenarios:
        if s.applies_to:
            continue  # cross-cutting rubric, second pass
        outcome = run_scenario(graph, s)
        outcomes[s.id] = outcome
        entry = {"title": s.title, "checks": evaluate_outcome(s, outcome)}
        if use_judge:
            entry["judge"] = score_scenario(s.title, s.judge_criteria, outcome,
                                            answer_key=_answer_key(s), llm=llm)
        results[s.id] = entry

    for s in scenarios:
        if not s.applies_to:
            continue
        per_target = {}
        for target in s.applies_to:
            if target not in outcomes:
                continue
            if not outcomes[target].get("draft"):
                per_target[target] = {"skipped": "no draft produced"}
            elif use_judge:
                per_target[target] = score_scenario(
                    f"{s.title} (draft from {target})", s.judge_criteria,
                    outcomes[target], llm=llm)
        results[s.id] = {"title": s.title, "cross_cutting": True, "judge": per_target}

    return {"scenarios": results}


def render_markdown(report: dict) -> str:
    lines = ["### Agent scenario eval (deterministic checks + gpt-5.1 judge)", "",
             "| scenario | route | flags | extra turns | judge pass/partial/fail |",
             "|---|---|---|---|---|"]
    for sid, r in report["scenarios"].items():
        if r.get("cross_cutting"):
            agg = {"pass": 0, "partial": 0, "fail": 0}
            for v in r["judge"].values():
                for k in agg:
                    agg[k] += v.get("counts", {}).get(k, 0)
            lines.append(f"| {sid} (cross-cutting) | — | — | — | "
                         f"{agg['pass']}/{agg['partial']}/{agg['fail']} |")
            continue
        c = r["checks"]
        route = "✓" if c["route_ok"] else f"✗ ({c['actual_route']}≠{c['expected_route']})"
        flags = "✓" if c["flags"]["ok"] else "✗"
        counts = r.get("judge", {}).get("counts", {})
        j = f"{counts.get('pass', '—')}/{counts.get('partial', '—')}/{counts.get('fail', '—')}"
        lines.append(f"| {sid} | {route} | {flags} | {c['extra_turns']} | {j} |")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", help="run a single scenario id")
    parser.add_argument("--no-judge", action="store_true",
                        help="deterministic checks only (skips gpt-5.1)")
    parser.add_argument("--json", help="also write the full report to this path")
    args = parser.parse_args()

    report = run(only=args.only, use_judge=not args.no_judge)
    print(render_markdown(report))
    if args.json:
        with open(args.json, "w") as f:
            json.dump(report, f, indent=2)
        print(f"\n(full report -> {args.json})")


if __name__ == "__main__":
    main()
