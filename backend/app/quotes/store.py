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
    status             TEXT NOT NULL DEFAULT 'pending_review',
    estimator_edit_md  TEXT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (thread_id, version)
);
ALTER TABLE quote_drafts ADD COLUMN IF NOT EXISTS contractor_id TEXT;
CREATE INDEX IF NOT EXISTS idx_quote_drafts_status
    ON quote_drafts (status);
"""

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
                     routing_packet: dict | None = None) -> dict[str, Any]:
        """Insert the next version for this thread, superseding any active one."""
        with self._conn() as c:
            c.execute(
                "UPDATE quote_drafts SET status = 'superseded', updated_at = now() "
                "WHERE thread_id = %s AND status = ANY(%s)",
                (thread_id, list(ACTIVE_STATUSES)))
            return c.execute(
                "INSERT INTO quote_drafts "
                "(thread_id, contractor_id, version, draft_md, routing_packet) "
                "VALUES (%s, %s, COALESCE((SELECT max(version) FROM quote_drafts "
                "                          WHERE thread_id = %s), 0) + 1, %s, %s) "
                "RETURNING *",
                (thread_id, settings.contractor_id, thread_id, draft_md,
                 json.dumps(routing_packet) if routing_packet else None),
            ).fetchone()

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

    def approve(self, quote_id: int) -> dict[str, Any] | None:
        with self._conn() as c:
            return c.execute(
                "UPDATE quote_drafts SET status = 'approved', updated_at = now() "
                "WHERE id = %s AND status = ANY(%s) RETURNING *",
                (quote_id, list(ACTIVE_STATUSES))).fetchone()
