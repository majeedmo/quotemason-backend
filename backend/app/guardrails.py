"""Duplicate-quote guardrails.

Two checks, both scoped to `settings.quote_expiry_days` and evaluated
synchronously in POST /chat -- before the codes->takeoff->price_fill->draft
pipeline is scheduled, so a blocked request never spends LLM/Tavily budget on
a draft that gets discarded:

1. Same property (scope + address) already has an active quote -> block.
   "Active" means any status other than "superseded"; an approved quote for
   the same job last week is still "you already have a live quote for this."
2. The same contact (email or phone) has started quotes for too many
   distinct properties in the window -> block (spam/bot velocity check;
   different addresses aren't the same-property case above).

See docs/capstone-progress.md for the design rationale.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from app.config import settings
from app.quotes.store import QuoteStore

_WS = re.compile(r"\s+")
_NON_DIGIT = re.compile(r"\D+")


def normalize_email(email: str | None) -> str | None:
    email = (email or "").strip().lower()
    return email or None


def normalize_phone(phone: str | None) -> str | None:
    digits = _NON_DIGIT.sub("", phone or "")
    return digits or None


def property_key(scope: str | None, property_location: str | None) -> str | None:
    """Normalized dedup key for "the same job": scope + address, not address
    alone, so two distinct projects at one property (e.g. a kitchen quote
    and a separate basement quote) don't collide."""
    scope = _WS.sub(" ", (scope or "").strip().lower())
    location = _WS.sub(" ", (property_location or "").strip().lower())
    if not scope or not location:
        return None
    return f"{scope}|{location}"


def check_duplicate(store: QuoteStore, slots: dict,
                    contact: dict[str, str] | None) -> str | None:
    """Returns a block reason ("duplicate_property" / "contact_rate_limit"),
    or None if the request may proceed to drafting."""
    contact = contact or {}
    email = normalize_email(contact.get("email"))
    phone = normalize_phone(contact.get("phone"))
    key = property_key(slots.get("scope"), slots.get("property_location"))
    if key is None:
        return None  # can't dedup without a property to key on

    since = datetime.now(timezone.utc) - timedelta(days=settings.quote_expiry_days)

    if store.find_active_duplicate(key, since):
        return "duplicate_property"

    if email or phone:
        n_existing = store.count_recent_properties_for_contact(email, phone, since)
        if n_existing + 1 > settings.max_quotes_per_contact_window:
            return "contact_rate_limit"

    return None
