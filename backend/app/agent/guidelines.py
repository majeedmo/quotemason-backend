"""Runtime loader for the builder guideline doc — the source of truth.

CLAUDE.md: guideline doc §6 defines the two-tier manual-intervention triggers;
"implement routing from that section's keyword lists, they are the source of
truth, not code constants". So this module parses the doc at import time:
extend the lists in the doc, not here. §3 (intake slots) and §5 (quoting
rules) are likewise injected verbatim into prompts from the doc.
"""

from __future__ import annotations

import re
from functools import lru_cache

from app.contractor import get_contractor

_QUOTED = re.compile(r"[\"“]([^\"”]+)[\"”]")


def guideline_doc():
    """This contractor's guideline doc (path from the contractor profile)."""
    return get_contractor().guideline_doc


@lru_cache(maxsize=1)
def _text() -> str:
    return guideline_doc().read_text()


@lru_cache(maxsize=8)
def section(num: str) -> str:
    """Full text of a top-level '## N.' section (or '### N.M' subsection)."""
    hashes = "##" if "." not in num else "###"
    m = re.search(rf"^{hashes} {re.escape(num)}[. ].*?$", _text(), re.M)
    if not m:
        raise KeyError(f"section {num} not found in {guideline_doc().name}")
    rest = _text()[m.start():]
    nxt = re.search(rf"^#{{2,{len(hashes)}}} ", rest[m.end() - m.start():], re.M)
    return rest[: (m.end() - m.start()) + nxt.start()] if nxt else rest


def _expand_slashes(kw: str) -> list[str]:
    """'lowering the floor/basement' -> both variants; single '/' families only."""
    if "/" not in kw:
        return [kw]
    head, *alts = kw.split("/")
    base = head.split()
    variants = [head.strip()]
    for a in alts:
        variants.append(" ".join(base[:-1] + [a.strip()]).strip())
    return [v for v in variants if v]


@lru_cache(maxsize=1)
def hard_route_keywords() -> dict[str, list[str]]:
    """Category -> quoted keywords from the §6.1 table (deterministic layer).
    Unquoted signals (GFA bands, budget floors) are semantic — left to the
    intake model's judgment layer, which sees §6 verbatim."""
    out: dict[str, list[str]] = {}
    for line in section("6.1").splitlines():
        if not line.startswith("|") or line.startswith("|---") or "Keywords / signals" in line:
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 3:
            continue
        kws = [v for q in _QUOTED.findall(cells[2]) for v in _expand_slashes(q)]
        if kws:
            out[cells[0]] = kws
    return out


_SUFFIXES = ("ning", "ing", "ed", "es", "s")


def _root(word: str) -> str:
    for suf in _SUFFIXES:
        if word.endswith(suf) and len(word) - len(suf) >= 4:
            return word[: -len(suf)]
    return word


def scan_hard_triggers(text: str) -> list[tuple[str, str]]:
    """Case-insensitive stem-ish scan per §6 ('underpin' catches
    'underpinning'). Returns (category, matched keyword) pairs."""
    low = text.lower()
    hits = []
    for cat, kws in hard_route_keywords().items():
        for kw in kws:
            k = kw.lower()
            words = k.split()
            words[-1] = _root(words[-1])
            if k in low or " ".join(words) in low:
                hits.append((cat, kw))
                break
    return hits
