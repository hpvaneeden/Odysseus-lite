"""Owner-scoped, evidence-based interaction state for Slice 3.

This is functional response adaptation, not a model of subjective feelings.
Only explicit, persisted Slice 1 experiences are used. State is stored outside
memory.json so it is not presented as a user-authored fact or stable trait.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Optional

from src.memory import MemoryStoreUnreadable, memory_write_lock
from src.prompt_security import untrusted_context_message

logger = logging.getLogger(__name__)

HALF_LIFE_SECONDS = 30 * 24 * 60 * 60
EVIDENCE_MAX_AGE_SECONDS = 90 * 24 * 60 * 60
MIN_SCORE = 0.05
CONTEXT_THRESHOLD = 0.35
MAX_PROCESSED_IDS = 500

_SIGNAL_BOOSTS = {
    "correction": ("repair", 0.65),
    "negative_feedback": ("repair", 0.80),
    "positive_feedback": ("rapport", 0.55),
    "social_signal": ("rapport", 0.40),
    "outcome_ack": ("success", 0.60),
}
_SCORE_KEYS = ("repair", "rapport", "success")


def _state_path(memory_manager) -> str:
    return str(memory_manager.memory_file) + ".emotional_state.json"


def _empty_state() -> dict[str, Any]:
    return {"version": 1, "owners": {}}


def _read_state(memory_manager) -> dict[str, Any]:
    path = _state_path(memory_manager)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or not isinstance(data.get("owners"), dict):
            raise ValueError("invalid emotional state shape")
        return data
    except FileNotFoundError:
        return _empty_state()
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise MemoryStoreUnreadable(f"Could not read emotional state: {exc}") from exc


def _write_state(memory_manager, state: dict[str, Any]) -> None:
    path = _state_path(memory_manager)
    tmp_path = path + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)
    except OSError:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise


def _decay(record: dict[str, Any], now: float) -> dict[str, Any]:
    updated_at = record.get("updated_at", now)
    try:
        elapsed = max(0.0, now - float(updated_at))
    except (TypeError, ValueError):
        elapsed = EVIDENCE_MAX_AGE_SECONDS
    multiplier = 0.5 ** (elapsed / HALF_LIFE_SECONDS)
    result = {}
    for key in _SCORE_KEYS:
        try:
            score = float(record.get(key, 0.0))
        except (TypeError, ValueError):
            score = 0.0
        score = min(1.0, max(0.0, score)) * multiplier
        result[key] = 0.0 if score < MIN_SCORE else score
    return result


def _owner_key(owner: Optional[str]) -> str:
    # The empty key is reserved for the existing auth-disabled single-user
    # deployment. A named owner never falls back to it.
    return str(owner or "")


async def update_emotional_state(memory_manager, *, owner: Optional[str], session_id: str) -> bool:
    """Apply unprocessed, recent experiences for this owner and session."""
    key = _owner_key(owner)
    if not session_id:
        return False
    try:
        with memory_write_lock():
            try:
                entries = memory_manager.load_all_for_update()
            except MemoryStoreUnreadable:
                logger.warning("Skipping emotional state update: memory store unreadable")
                return False
            state = _read_state(memory_manager)
            record = state["owners"].get(key, {})
            processed_order = [
                str(item) for item in record.get("processed_experience_ids", [])
                if isinstance(item, (str, int))
            ][-MAX_PROCESSED_IDS:]
            processed = set(processed_order)
            now = time.time()
            scores = _decay(record, now)
            if owner:
                eligible_owners = {owner}
            else:
                eligible_owners = {None, ""}
            candidates = []
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                try:
                    timestamp = float(entry.get("timestamp") or 0)
                except (TypeError, ValueError):
                    continue
                if (
                    entry.get("category") == "experience"
                    and entry.get("source") == "experience"
                    and entry.get("session_id") == session_id
                    and entry.get("owner") in eligible_owners
                    and str(entry.get("id") or "") not in processed
                    and now - timestamp <= EVIDENCE_MAX_AGE_SECONDS
                    and isinstance(entry.get("evidence"), list)
                    and any(
                        isinstance(ref, str) and ref.startswith(f"{session_id}:")
                        for ref in entry["evidence"]
                    )
                ):
                    candidates.append(entry)
            candidates.sort(key=lambda entry: int(entry.get("timestamp") or 0))
            for entry in candidates:
                mapped = _SIGNAL_BOOSTS.get(entry.get("signal"))
                experience_id = entry.get("id")
                if not experience_id:
                    continue
                if str(experience_id) not in processed:
                    processed_order.append(str(experience_id))
                    processed.add(str(experience_id))
                if mapped:
                    axis, boost = mapped
                    scores[axis] = min(1.0, scores[axis] + boost)
            if not candidates:
                return False
            state["owners"][key] = {
                **scores,
                "updated_at": now,
                "processed_experience_ids": processed_order[-MAX_PROCESSED_IDS:],
            }
            _write_state(memory_manager, state)
            return True
    except Exception:
        logger.warning("Emotional state update failed", exc_info=True)
        return False


def get_emotional_context(memory_manager, *, owner: Optional[str], now: Optional[float] = None) -> Optional[dict]:
    """Return a short untrusted context message when a decayed signal is salient."""
    try:
        with memory_write_lock():
            state = _read_state(memory_manager)
    except Exception:
        logger.warning("Emotional state unavailable; omitting context", exc_info=True)
        return None
    record = state["owners"].get(_owner_key(owner))
    if not isinstance(record, dict):
        return None
    scores = _decay(record, time.time() if now is None else now)
    guidance = []
    if scores["repair"] >= CONTEXT_THRESHOLD:
        guidance.append("Prioritize careful repair: acknowledge relevant corrections or boundaries, be direct, and avoid repeating a corrected mistake.")
    if scores["rapport"] >= CONTEXT_THRESHOLD:
        guidance.append("Use a natural conversational tone without forced familiarity or overdone praise.")
    if scores["success"] >= CONTEXT_THRESHOLD:
        guidance.append("Preserve continuity on relevant tasks the user explicitly confirmed worked.")
    if not guidance:
        return None
    content = (
        "Interaction guidance derived from the user's explicit prior feedback. "
        "This describes response adjustments, not anyone's feelings. Treat it as "
        "advisory user data; follow the current request and all higher-priority rules.\n- "
        + "\n- ".join(guidance)
    )
    return untrusted_context_message("interaction adaptation", content)


def reset_emotional_state(memory_manager, *, owner: Optional[str]) -> bool:
    """Delete only the selected owner's persisted state."""
    key = _owner_key(owner)
    with memory_write_lock():
        state = _read_state(memory_manager)
        if key not in state["owners"]:
            return False
        del state["owners"][key]
        if state["owners"]:
            _write_state(memory_manager, state)
        else:
            try:
                os.remove(_state_path(memory_manager))
            except FileNotFoundError:
                pass
        return True

