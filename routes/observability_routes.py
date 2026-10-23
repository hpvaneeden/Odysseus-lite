"""Observability log viewer routes — read-only, admin-only.

"""

from __future__ import annotations

import json
import logging
import os

from fastapi import APIRouter, HTTPException, Query, Request

from core.middleware import require_admin
from src import observability as obs

logger = logging.getLogger(__name__)

router = APIRouter(tags=["observability"])

# ── Configuration ──────────────────────────────────────────────────────────

# (id, label) pairs matching the event types written by src.observability.py.
# The client may only request these IDs — arbitrary filesystem paths are
# never accepted from the browser.
KNOWN_CATEGORIES: tuple[tuple[str, str], ...] = (
    ("model_input", "Model inputs"),
    ("state_snapshot", "State snapshots"),
    ("memory_audit", "Memory audit"),
    ("naming_event", "Naming events"),
    ("self_def_event", "Self-definition events"),
)

_CATEGORY_IDS = {cat_id for cat_id, _ in KNOWN_CATEGORIES}

MAX_READ_BYTES = 2 * 1024 * 1024      # hard cap on bytes read from one file
DEFAULT_LIMIT = 50                    # default entries returned
MAX_LIMIT = 500                       # absolute client-requestable cap

# ── Endpoints ──────────────────────────────────────────────────────────────

@router.get("/api/observability/entries")
async def get_entries(
    request: Request,
    category: str = Query(default=""),
    limit: int = Query(default=DEFAULT_LIMIT),
) -> dict:
    """Return the latest entries for a log category, newest first."""
    require_admin(request)
    if category not in _CATEGORY_IDS:
        raise HTTPException(400, f"Unknown log category: {category!r}")
    try:
        limit = max(1, min(int(limit), MAX_LIMIT))
    except (TypeError, ValueError):
        limit = DEFAULT_LIMIT

    path = obs._log_file(category)
    # Defense in depth: never read outside the observability directory.
    dir_real = os.path.realpath(obs.OBSERVABILITY_DIR)
    path_real = os.path.realpath(path)
    if path_real != dir_real and not path_real.startswith(dir_real + os.sep):
        raise HTTPException(400, "Invalid log category")
    if not os.path.exists(path):
        return {
            "status": "success",
            "category": category,
            "entries": [],
            "count": 0,
            "malformed": 0,
            "truncated": False,
        }

    try:
        entries, malformed, truncated = _read_tail_entries(path, limit)
        entries.sort(key=lambda e: e.get("timestamp") or "", reverse=True)
        return {
            "status": "success",
            "category": category,
            "entries": entries,
            "count": len(entries),
            "malformed": malformed,
            "truncated": truncated,
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to read observability log %s: %s", category, exc)
        raise HTTPException(500, "Unable to read log file")


# ── Bounded read helpers ───────────────────────────────────────────────────

def _read_tail_entries(path: str, limit: int) -> tuple[list[dict], int, bool]:
    """Read up to ``limit`` JSONL entries from the tail of ``path``.

    Bytes read are bounded to MAX_READ_BYTES: for files larger than the cap we
    seek from the end and discard the first (possibly torn) line, so a huge or
    actively appended log cannot stall the application. Malformed lines are
    skipped and counted.
    """
    size = os.path.getsize(path)
    truncated = size > MAX_READ_BYTES

    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        if truncated:
            fh.seek(max(0, size - MAX_READ_BYTES))
            fh.readline()  # drop the possibly-partial first line
        data = fh.read()

    entries: list[dict] = []
    malformed = 0
    for line in data.splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            malformed += 1
            continue
        if isinstance(entry, dict):
            entries.append(entry)

    # File order is chronological; keep the most recent `limit` entries.
    if len(entries) > limit:
        entries = entries[-limit:]
    return entries, malformed, truncated



@router.get("/api/observability/categories")
async def list_categories(request: Request) -> dict:
    """List the log categories that have files in OBSERVABILITY_DIR."""
    require_admin(request)
    try:
        dir_path = obs.OBSERVABILITY_DIR
        available: set[str] = set()
        if os.path.isdir(dir_path):
            for name in os.listdir(dir_path):
                if not name.endswith(".jsonl"):
                    continue
                event_type = name[: -len(".jsonl")]
                if event_type in _CATEGORY_IDS:
                    available.add(event_type)
        categories = [
            {"id": cat_id, "label": label}
            for cat_id, label in KNOWN_CATEGORIES
            if cat_id in available
        ]
        return {"status": "success", "categories": categories}
    except Exception as exc:  # pragma: no cover - defensive
        logger.error("Failed to list observability categories: %s", exc)
        return {"status": "error", "error": "Unable to list log categories", "categories": []}