"""Duplicate-quote guardrail tests — no network, no DB."""

from __future__ import annotations

from datetime import datetime, timezone

from app.config import settings
from app.guardrails import (check_duplicate, normalize_email, normalize_phone,
                            property_key)


def test_normalize_email():
    assert normalize_email(" Bob@Example.com ") == "bob@example.com"
    assert normalize_email(None) is None
    assert normalize_email("") is None
    assert normalize_email("   ") is None


def test_normalize_phone():
    assert normalize_phone("(555) 123-4567") == "5551234567"
    assert normalize_phone("555.123.4567 x2") == "55512345672"
    assert normalize_phone(None) is None
    assert normalize_phone("---") is None


def test_property_key_combines_scope_and_address_normalized():
    assert (property_key("Basement", "  123 Main St, Cambridge  ")
            == "basement|123 main st, cambridge")
    # collapses internal whitespace too
    assert property_key("basement", "123   Main   St") == "basement|123 main st"


def test_property_key_none_when_either_half_missing():
    assert property_key(None, "123 Main St") is None
    assert property_key("basement", None) is None
    assert property_key("", "") is None


class _FakeStore:
    def __init__(self, duplicate=None, contact_count=0):
        self._duplicate = duplicate
        self._contact_count = contact_count
        self.duplicate_calls = []
        self.contact_calls = []

    def find_active_duplicate(self, property_key, since):
        self.duplicate_calls.append((property_key, since))
        return self._duplicate

    def count_recent_properties_for_contact(self, email, phone, since):
        self.contact_calls.append((email, phone, since))
        return self._contact_count


_SLOTS = {"scope": "basement", "property_location": "123 Main St"}


def test_check_duplicate_no_property_key_is_a_no_op():
    store = _FakeStore()
    assert check_duplicate(store, {}, {"email": "a@b.com"}) is None
    assert store.duplicate_calls == []  # never even queries without a key


def test_check_duplicate_blocks_on_active_duplicate():
    store = _FakeStore(duplicate={"id": 1, "status": "pending_review"})
    assert check_duplicate(store, _SLOTS, None) == "duplicate_property"


def test_check_duplicate_skips_contact_check_when_no_contact_given():
    store = _FakeStore(duplicate=None)
    assert check_duplicate(store, _SLOTS, None) is None
    assert store.contact_calls == []


def test_check_duplicate_blocks_at_contact_rate_limit(monkeypatch):
    monkeypatch.setattr(settings, "max_quotes_per_contact_window", 3)
    store = _FakeStore(duplicate=None, contact_count=3)  # already at the cap
    reason = check_duplicate(store, _SLOTS, {"email": "spammer@x.com"})
    assert reason == "contact_rate_limit"


def test_check_duplicate_allows_under_the_cap(monkeypatch):
    monkeypatch.setattr(settings, "max_quotes_per_contact_window", 3)
    store = _FakeStore(duplicate=None, contact_count=2)  # this one would be the 3rd
    assert check_duplicate(store, _SLOTS, {"email": "ok@x.com"}) is None


def test_check_duplicate_uses_normalized_email_and_phone(monkeypatch):
    monkeypatch.setattr(settings, "max_quotes_per_contact_window", 5)
    store = _FakeStore(duplicate=None, contact_count=0)
    check_duplicate(store, _SLOTS, {"email": " Bob@Example.com ", "phone": "(555) 123-4567"})
    email, phone, since = store.contact_calls[0]
    assert email == "bob@example.com"
    assert phone == "5551234567"
    assert isinstance(since, datetime) and since.tzinfo is not None


def test_check_duplicate_since_reflects_configured_expiry(monkeypatch):
    monkeypatch.setattr(settings, "quote_expiry_days", 30)
    store = _FakeStore(duplicate=None)
    check_duplicate(store, _SLOTS, None)
    _, since = store.duplicate_calls[0]
    age_days = (datetime.now(timezone.utc) - since).days
    assert age_days in (29, 30)  # ~30 days ago, allowing for test-run jitter
