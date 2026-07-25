"""Chat model factories — OpenRouter for intake/drafting (CLAUDE.md model
table), with the brief's fallback routing via OpenRouter's `models` array."""

from __future__ import annotations

from langchain_openai import ChatOpenAI

from app.config import settings

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

_FALLBACKS = {  # brief, Task 2 model table
    "intake": ["google/gemini-2.5-flash"],
    "codes": ["google/gemini-2.5-flash"],
    "drafting": ["openai/gpt-5.1"],
    "takeoff": ["google/gemini-2.5-flash"],
}


def _chat(model: str, role: str, temperature: float) -> ChatOpenAI:
    return ChatOpenAI(
        model=model,
        api_key=settings.openrouter_api_key,
        base_url=OPENROUTER_BASE_URL,
        temperature=temperature,
        extra_body={"models": _FALLBACKS[role]},
    )


def intake_model() -> ChatOpenAI:
    return _chat(settings.intake_model, "intake", temperature=0.2)


def codes_model() -> ChatOpenAI:
    """Stage-1 codes checklist (tool-calling over the regulatory service).
    Same tier as intake but a DISTINCT factory: tests trap intake_model to
    prove revisions skip intake, and the codes stage must not trip it."""
    return _chat(settings.intake_model, "codes", temperature=0.1)


def drafting_model() -> ChatOpenAI:
    return _chat(settings.drafting_model, "drafting", temperature=0.3)


def takeoff_model() -> ChatOpenAI:
    """Cost experiment 2026-07-25: cheaper model for the takeoff stage only
    -- structured quantity extraction, arguably not requiring the top-tier
    model draft_node's client-facing prose does. Own settings.takeoff_model
    (currently the same value as intake_model, but independently
    configurable -- takeoff's accuracy/cost trade-off is its own decision,
    not tied to whatever intake happens to run on). Validated against the
    quote-accuracy eval before trusting it (see docs/capstone-progress.md);
    draft stays on drafting_model regardless."""
    return _chat(settings.takeoff_model, "takeoff", temperature=0.1)
