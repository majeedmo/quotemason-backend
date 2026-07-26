"""Neon Postgres store for drafted quotes — the estimator review gate.

Every draft the agent produces lands here as a versioned row; the estimator
reviews from this table, never from the chat stream. Status lifecycle:

    pending_review -> edited -> approved
                 \-> superseded (a /revise run produced a newer version)

Only pending_review/edited rows are actionable; approved and superseded are
terminal. One row per (thread_id, version); a revision inserts version n+1
and supersedes the active row, so the full draft history is auditable.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import psycopg
from psycopg.rows import dict_row

from app.config import settings

_SCHEMA = """
CREATE TABLE IF NOT EXISTS quote_drafts (
    id                 SERIAL PRIMARY KEY,
    thread_id          TEXT NOT NULL,
    contractor_id      TEXT,
    version            INTEGER NOT NULL,
    draft_md           TEXT NOT NULL,
    routing_packet     JSONB,
    stage_outputs      JSONB,
    status             TEXT NOT NULL DEFAULT 'pending_review',
    estimator_edit_md  TEXT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (thread_id, version)
);
ALTER TABLE quote_drafts ADD COLUMN IF NOT EXISTS contractor_id TEXT;
ALTER TABLE quote_drafts ADD COLUMN IF NOT EXISTS stage_outputs JSONB;
ALTER TABLE quote_drafts ADD COLUMN IF NOT EXISTS contact_email TEXT;
ALTER TABLE quote_drafts ADD COLUMN IF NOT EXISTS contact_phone TEXT;
ALTER TABLE quote_drafts ADD COLUMN IF NOT EXISTS property_key TEXT;
CREATE INDEX IF NOT EXISTS idx_quote_drafts_status
    ON quote_drafts (status);
CREATE INDEX IF NOT EXISTS idx_quote_drafts_property_key
    ON quote_drafts (property_key);
CREATE INDEX IF NOT EXISTS idx_quote_drafts_contact_email
    ON quote_drafts (contact_email);
CREATE INDEX IF NOT EXISTS idx_quote_drafts_contact_phone
    ON quote_drafts (contact_phone);

-- Append-only audit log for estimator single-line price overrides (never
-- updated or deleted) -- the substrate for eval/audit once overrides are
-- allowed on any line, not just unpriced ones.
CREATE TABLE IF NOT EXISTS price_overrides (
    id                  SERIAL PRIMARY KEY,
    thread_id           TEXT NOT NULL,
    takeoff_line_ref    TEXT NOT NULL,
    price_cad           NUMERIC NOT NULL,
    note                TEXT,
    price_source_before TEXT NOT NULL,
    source_quote_id     INTEGER NOT NULL,
    result_quote_id     INTEGER NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_price_overrides_thread
    ON price_overrides (thread_id);

-- One row per generation event (initial draft, each revision, each price
-- override) -- not per LLM call, that granularity stays in
-- quote_drafts.stage_outputs.generation_stats for drill-down. Per-event,
-- incremental values only, so dashboard aggregation (SUM/AVG over a time
-- window) is plain arithmetic with no cumulative-double-counting risk.
CREATE TABLE IF NOT EXISTS quote_generation_events (
    id                SERIAL PRIMARY KEY,
    quote_id          INTEGER NOT NULL,
    thread_id         TEXT NOT NULL,
    version           INTEGER NOT NULL,
    trigger           TEXT NOT NULL,
    duration_seconds  NUMERIC NOT NULL,
    cost_usd          NUMERIC,
    cost_is_complete  BOOLEAN NOT NULL DEFAULT TRUE,
    input_tokens      INTEGER NOT NULL DEFAULT 0,
    output_tokens     INTEGER NOT NULL DEFAULT 0,
    llm_calls         INTEGER NOT NULL DEFAULT 0,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_quote_generation_events_created_at
    ON quote_generation_events (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_quote_generation_events_thread_id
    ON quote_generation_events (thread_id);
"""

# Guardrail queries (find_active_duplicate, count_recent_properties_for_contact)
# treat every non-superseded row as "still an active quote" -- an approved
# and presumably-sent quote for the same property last week is still "you
# already have a live quote for this"; only a superseded (revised) row
# should stop counting.
_NOT_SUPERSEDED = "status != 'superseded'"

ACTIVE_STATUSES = ("pending_review", "edited")


class QuoteStore:
    def __init__(self, dsn: str | None = None):
        self.dsn = dsn or settings.database_url
        if not self.dsn:
            raise RuntimeError("DATABASE_URL is not set")

    def _conn(self) -> psycopg.Connection:
        # Connection per operation: fine at challenge scale, and Neon's
        # serverless pooler dislikes long-lived idle connections anyway.
        return psycopg.connect(self.dsn, row_factory=dict_row)

    def ensure_schema(self) -> None:
        with self._conn() as c:
            c.execute(_SCHEMA)

    def create_draft(self, thread_id: str, draft_md: str,
                     routing_packet: dict | None = None,
                     stage_outputs: dict | None = None,
                     contact_email: str | None = None,
                     contact_phone: str | None = None,
                     property_key: str | None = None) -> dict[str, Any]:
        """Insert the next version for this thread, superseding any active one."""
        with self._conn() as c:
            c.execute(
                "UPDATE quote_drafts SET status = 'superseded', updated_at = now() "
                "WHERE thread_id = %s AND status = ANY(%s)",
                (thread_id, list(ACTIVE_STATUSES)))
            return c.execute(
                "INSERT INTO quote_drafts (thread_id, contractor_id, version, "
                "draft_md, routing_packet, stage_outputs, contact_email, "
                "contact_phone, property_key) "
                "VALUES (%s, %s, COALESCE((SELECT max(version) FROM quote_drafts "
                "                          WHERE thread_id = %s), 0) + 1, "
                "%s, %s, %s, %s, %s, %s) "
                "RETURNING *",
                (thread_id, settings.contractor_id, thread_id, draft_md,
                 json.dumps(routing_packet) if routing_packet else None,
                 json.dumps(stage_outputs) if stage_outputs else None,
                 contact_email, contact_phone, property_key),
            ).fetchone()

    def find_active_duplicate(self, property_key: str,
                              since: datetime) -> dict[str, Any] | None:
        """Most recent non-superseded row for this property since `since` --
        the guardrail against quoting the same job twice before it expires."""
        with self._conn() as c:
            return c.execute(
                f"SELECT * FROM quote_drafts WHERE property_key = %s "
                f"AND created_at > %s AND {_NOT_SUPERSEDED} "
                f"ORDER BY created_at DESC LIMIT 1",
                (property_key, since)).fetchone()

    def count_recent_properties_for_contact(self, email: str | None,
                                            phone: str | None,
                                            since: datetime) -> int:
        """Distinct properties this contact (by email or phone) has started
        a quote for since `since` -- the velocity guardrail against one
        contact bombarding the system with many different fake addresses."""
        if not email and not phone:
            return 0
        with self._conn() as c:
            row = c.execute(
                f"SELECT COUNT(DISTINCT property_key) AS n FROM quote_drafts "
                f"WHERE (contact_email = %s OR contact_phone = %s) "
                f"AND property_key IS NOT NULL AND created_at > %s "
                f"AND {_NOT_SUPERSEDED}",
                (email, phone, since)).fetchone()
            return row["n"] if row else 0

    def get(self, quote_id: int) -> dict[str, Any] | None:
        with self._conn() as c:
            return c.execute("SELECT * FROM quote_drafts WHERE id = %s",
                             (quote_id,)).fetchone()

    def list(self, status: str | None = None) -> list[dict[str, Any]]:
        with self._conn() as c:
            if status:
                cur = c.execute("SELECT * FROM quote_drafts WHERE status = %s "
                                "ORDER BY updated_at DESC", (status,))
            else:
                cur = c.execute("SELECT * FROM quote_drafts "
                                "ORDER BY updated_at DESC")
            return cur.fetchall()

    def save_edit(self, quote_id: int, edited_md: str) -> dict[str, Any] | None:
        """Store the estimator's edited version. Only active rows are editable."""
        with self._conn() as c:
            return c.execute(
                "UPDATE quote_drafts SET estimator_edit_md = %s, "
                "status = 'edited', updated_at = now() "
                "WHERE id = %s AND status = ANY(%s) RETURNING *",
                (edited_md, quote_id, list(ACTIVE_STATUSES))).fetchone()

    def record_price_override(self, thread_id: str, takeoff_line_ref: str,
                              price_cad: float, note: str | None,
                              price_source_before: str, source_quote_id: int,
                              result_quote_id: int) -> dict[str, Any]:
        """Append-only audit row for a single-line estimator price override."""
        with self._conn() as c:
            return c.execute(
                "INSERT INTO price_overrides (thread_id, takeoff_line_ref, "
                "price_cad, note, price_source_before, source_quote_id, "
                "result_quote_id) VALUES (%s, %s, %s, %s, %s, %s, %s) "
                "RETURNING *",
                (thread_id, takeoff_line_ref, price_cad, note,
                 price_source_before, source_quote_id, result_quote_id),
            ).fetchone()

    def record_generation_event(self, quote_id: int, thread_id: str, version: int,
                                trigger: str, duration_seconds: float,
                                total_cost_usd: float | None, cost_is_complete: bool,
                                total_input_tokens: int, total_output_tokens: int,
                                llm_calls: int) -> dict[str, Any]:
        """Append-only dashboard row for one generation event (initial draft,
        a revision, or a price override) -- this event's own incremental
        cost/usage, not the quote's running cumulative total."""
        with self._conn() as c:
            return c.execute(
                "INSERT INTO quote_generation_events (quote_id, thread_id, version, "
                "trigger, duration_seconds, cost_usd, cost_is_complete, input_tokens, "
                "output_tokens, llm_calls) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "RETURNING *",
                (quote_id, thread_id, version, trigger, duration_seconds,
                 total_cost_usd, cost_is_complete, total_input_tokens,
                 total_output_tokens, llm_calls),
            ).fetchone()

    def generation_dashboard_stats(self, since: datetime, limit: int = 10) -> dict[str, Any]:
        """Aggregate totals + a recent-events list for the dashboard widget,
        one query pair serving both."""
        with self._conn() as c:
            totals = c.execute(
                "SELECT COUNT(*) AS count, "
                "COALESCE(SUM(cost_usd), 0) AS total_cost_usd, "
                "COALESCE(AVG(duration_seconds), 0) AS avg_duration_seconds, "
                "COALESCE(AVG(cost_usd), 0) AS avg_cost_usd "
                "FROM quote_generation_events WHERE created_at > %s",
                (since,)).fetchone()
            recent = c.execute(
                "SELECT * FROM quote_generation_events WHERE created_at > %s "
                "ORDER BY created_at DESC LIMIT %s",
                (since, limit)).fetchall()
            return {"totals": totals, "recent": recent}

    def approve(self, quote_id: int) -> dict[str, Any] | None:
        with self._conn() as c:
            return c.execute(
                "UPDATE quote_drafts SET status = 'approved', updated_at = now() "
                "WHERE id = %s AND status = ANY(%s) RETURNING *",
                (quote_id, list(ACTIVE_STATUSES))).fetchone()
