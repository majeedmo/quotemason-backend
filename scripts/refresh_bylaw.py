# /// script
# requires-python = ">=3.10"
# ///
"""Manual bylaw-refresh stub (shipped per project-brief.md "Data freshness").

The full pipeline (scheduled hash-diff polling, section-level diffing, human
review, versioned upsert) is capstone scope. What ships now is this manual
entry point plus the `effective_date`/`source_version` metadata every chunk
already carries.

Intended manual flow when a new bylaw/code version lands (e.g. the certified
By-law 26-007 arrives from planning@cambridge.ca):

  1. Drop the new PDF into corpus/cambridge-zoning-bylaw/ (or corpus/OBC/).
  2. Re-run the relevant extraction (scripts/extract_obc_part9_phase1.py or
     scripts/extract_zoning_bylaw.py) with an updated `source_version`.
  3. Re-run ingestion: cd backend && uv run python -m app.ingestion.ingest
     Deterministic chunk IDs make this an upsert; chunks whose section text
     changed get re-embedded under the new source_version, unchanged ones are
     overwritten in place.
  4. Superseded-version handling (mark-not-delete audit trail) is capstone
     scope — today a refresh simply replaces.

Run: uv run scripts/refresh_bylaw.py <path-to-new-document.pdf>
"""

import sys

if __name__ == "__main__":
    doc = sys.argv[1] if len(sys.argv) > 1 else "<no document given>"
    print(__doc__)
    print(f"Received: {doc}")
    print("Stub: follow the manual flow above. Automation is capstone scope.")
