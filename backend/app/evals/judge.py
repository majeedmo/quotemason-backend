"""Cross-family judge (Task 5 harness): gpt-5.1 scores agent outcomes against
each scenario's rubric criteria.

The judge is deliberately not an Anthropic model (drafts are written by
claude-sonnet-5 — self-preference bias), and deliberately has no fallback
array: the judge must be exactly ``settings.judge_model`` or the run fails.
"""

from __future__ import annotations

import json
import re

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from app.config import settings

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

_FENCE = re.compile(r"^```(?:json)?|```$", re.MULTILINE)
_JSON_START = re.compile(r"\{")

VERDICTS = ("pass", "partial", "fail")

_SYSTEM = """\
You are a strict evaluation judge for an AI estimation assistant used by a \
residential renovation contractor. You are given a test scenario, the \
assistant's actual behavior (conversation, routing decision, flags, and draft \
quote if one was produced), and a list of rubric criteria. Judge each \
criterion strictly against the evidence — quote the evidence, do not give \
benefit of the doubt, and never reward fabricated citations, invented \
comparables, or unsupported prices.

Answer with ONLY a JSON object, no prose around it:
{"criteria": [{"criterion": "<verbatim criterion>", "verdict": "pass|partial|fail",
  "evidence": "<short quote or observation>"}], "summary": "<1-2 sentences>"}
"""


def judge_model() -> ChatOpenAI:
    return ChatOpenAI(
        model=settings.judge_model,
        api_key=settings.openrouter_api_key,
        base_url=OPENROUTER_BASE_URL,
        temperature=0.0,
    )


def parse_verdicts(raw: str) -> dict:
    """Defensive JSON extraction (same resilience pattern as intake parsing)."""
    text = _FENCE.sub("", raw.strip())
    try:
        out = json.loads(text)
        if isinstance(out, dict) and "criteria" in out:
            return out
    except json.JSONDecodeError:
        pass
    dec = json.JSONDecoder()
    for m in _JSON_START.finditer(text):
        try:
            obj, _ = dec.raw_decode(text, m.start())
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and "criteria" in obj:
            return obj
    return {"criteria": [], "summary": "", "error": "judge output unparseable",
            "raw": raw[:2000]}


def _prompt(title: str, criteria: list[str], outcome: dict, answer_key: str | None) -> str:
    parts = [
        f"SCENARIO: {title}",
        "",
        "ACTUAL BEHAVIOR:",
        f"- route taken: {outcome.get('route')}",
        f"- extra clarification turns beyond the script: {outcome.get('extra_turns', 0)}",
        f"- flags on the draft: {json.dumps(outcome.get('flags') or [])}",
        "",
        "CONVERSATION (customer-visible):",
        outcome.get("transcript", "(none)"),
    ]
    if outcome.get("draft"):
        parts += ["", "DRAFT QUOTE (internal, estimator-facing):", outcome["draft"]]
    if answer_key:
        parts += ["", "GROUND-TRUTH ANSWER KEY (judge-only; the assistant has "
                      "never seen this document):", answer_key]
    parts += ["", "RUBRIC CRITERIA TO JUDGE:"]
    parts += [f"{i}. {c}" for i, c in enumerate(criteria, 1)]
    return "\n".join(parts)


def score_scenario(title: str, criteria: list[str], outcome: dict,
                   answer_key: str | None = None, llm: ChatOpenAI | None = None) -> dict:
    llm = llm or judge_model()
    raw = llm.invoke([SystemMessage(content=_SYSTEM),
                      HumanMessage(content=_prompt(title, criteria, outcome, answer_key))])
    verdicts = parse_verdicts(raw.content)
    counts = {v: 0 for v in VERDICTS}
    for c in verdicts.get("criteria", []):
        v = c.get("verdict")
        if v in counts:
            counts[v] += 1
    verdicts["counts"] = counts
    return verdicts
