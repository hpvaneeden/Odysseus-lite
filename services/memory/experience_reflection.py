"""
experience_reflection.py

Slice 2 of evolving social behaviour: controlled reflection on recorded
experiences to detect recurring patterns and persist them as learnable traits.

Design principles (mirroring Slice 1):
- Evidence-first: traits are derived only from real, persisted experience
  entries with verified message-id evidence. No invented examples.
- Conservative: requires multiple independent occurrences before a trait is
  created; a single event never forms a stable trait.
- Owner-isolated: one owner's experiences never influence another owner's
  traits. Same memory_write_lock discipline as Slice 1.
- Fail-soft: every path returns a count and never raises into the chat path.
- Inspectable/revisable: traits are ordinary memory.json entries
  (category="trait") — readable, queryable, replaceable, deletable.
- No base prompt or safety-rule rewriting: traits are advisory metadata,
  never injected into the system prompt or model instructions.
"""

import json
import logging
import os
import time
import uuid
from typing import Any, Dict, List, Optional

from src.memory import MemoryStoreUnreadable, memory_write_lock

logger = logging.getLogger(__name__)

TRAIT_CATEGORY = "trait"
TRAIT_SOURCE = "reflection"

# Minimum number of experiences of the same signal before a trait is created.
# A single event is never enough to establish a stable behavioural tendency.
_MIN_EVIDENCE_COUNT = 3

# Confidence grows with evidence count, asymptotically approaching 1.0.
# count=3 -> ~0.5, count=6 -> ~0.75, count=10+ -> ~0.9
def _confidence_for_count(count: int) -> float:
    return min(1.0, count / 6.0)

# Opposing signal pairs: if both appear for the same topic area, the trait
# is considered contested and confidence is reduced.
_OPPOSING_SIGNALS = {
    "positive_feedback": {"negative_feedback"},
    "negative_feedback": {"positive_feedback"},
    "outcome_ack": {"negative_feedback"},
    "negative_feedback": {"outcome_ack"},
}

# How far back to look for experiences when reflecting. Beyond this window,
# older evidence is considered stale and does not count toward new traits.
_REFLECTION_WINDOW_DAYS = 90

# How many recent experiences to scan per reflection run. Bounded so a user
# with thousands of experiences doesn't block the chat path.
_MAX_EXPERIENCES_TO_SCAN = 200


def _is_trait_entry(entry: Dict[str, Any]) -> bool:
    return isinstance(entry, dict) and entry.get("category") == TRAIT_CATEGORY


def _is_experience_entry(entry: Dict[str, Any]) -> bool:
    return isinstance(entry, dict) and entry.get("category") == "experience"


def _parse_ts(entry: Dict[str, Any]) -> float:
    return float(entry.get("timestamp", 0) or 0)


def _days_ago(ts: float) -> float:
    return (time.time() - ts) / 86400.0


def _signal_counts(experiences: List[Dict[str, Any]]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for e in experiences:
        sig = e.get("signal")
        if sig:
            counts[sig] = counts.get(sig, 0) + 1
    return counts


def _has_opposing_signals(experiences: List[Dict[str, Any]], signal: str) -> bool:
    opposers = _OPPOSING_SIGNALS.get(signal, set())
    if not opposers:
        return False
    for e in experiences:
        if e.get("signal") in opposers:
            return True
    return False


def _collect_evidence_ids(experiences: List[Dict[str, Any]], signal: str) -> List[str]:
    ids: List[str] = []
    for e in experiences:
        if e.get("signal") == signal:
            ev = e.get("evidence") or []
            for ref in ev:
                if isinstance(ref, str) and ref not in ids:
                    ids.append(ref)
    return ids


def _build_trait_entry(
    signal: str,
    count: int,
    evidence_ids: List[str],
    session_id: Optional[str],
    owner: Optional[str],
    existing_trait: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    confidence = _confidence_for_count(count)
    if existing_trait:
        # Revise upward only if new evidence raises confidence; never lower
        # an established trait's confidence from fresh data alone —
        # contradictory evidence is handled separately at the call site.
        old_conf = float(existing_trait.get("confidence", 0.0) or 0.0)
        if confidence > old_conf:
            existing_trait["confidence"] = confidence
        existing_trait["evidence_count"] = int(existing_trait.get("evidence_count", 0)) + count
        existing_trait["evidence_ids"] = _merge_unique(
            existing_trait.get("evidence_ids") or [], evidence_ids
        )
        existing_trait["revision_count"] = int(existing_trait.get("revision_count", 0) or 0) + 1
        existing_trait["last_revised"] = int(time.time())
        if session_id:
            existing_trait["session_id"] = str(session_id)
        return existing_trait

    return {
        "id": existing_trait.get("id") if existing_trait else None,
        "text": _trait_description(signal, count),
        "category": TRAIT_CATEGORY,
        "source": TRAIT_SOURCE,
        "owner": owner,
        "signal": signal,
        "confidence": confidence,
        "evidence_count": count,
        "evidence_ids": evidence_ids,
        "revision_count": 1,
        "session_id": str(session_id) if session_id else None,
        "created_at": int(time.time()),
        "last_revised": int(time.time()),
    }


def _merge_unique(existing: List[str], additions: List[str]) -> List[str]:
    seen = set(existing)
    merged = list(existing)
    for item in additions:
        if item not in seen:
            seen.add(item)
            merged.append(item)
    return merged


def _trait_description(signal: str, count: int) -> str:
    """Neutral, factual description. No mood inference."""
    return f"User has given {count} {signal.replace('_', ' ')} signals over time."


def _find_existing_trait(traits: List[Dict[str, Any]], signal: str) -> Optional[Dict[str, Any]]:
    for t in traits:
        if t.get("signal") == signal:
            return t
    return None


def _load_trait_state_path(memory_manager) -> str:
    """Sidecar next to memory.json recording which experience ids have been
    folded into traits, so reflection never re-processes the same evidence."""
    return memory_manager.memory_file + ".reflection_state.json"


def _load_reflection_state(memory_manager) -> dict:
    path = _load_trait_state_path(memory_manager)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _save_reflection_state(memory_manager, state: dict) -> None:
    path = _load_trait_state_path(memory_manager)
    tmp_path = path + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
        os.replace(tmp_path, path)
    except OSError as e:
        logger.warning("Could not persist reflection state: %s", e)
        try:
            os.remove(tmp_path)
        except OSError:
            pass


def _processed_ids(state: dict, owner: Optional[str]) -> set:
    return set(state.get(owner or "", {}).get("processed_experience_ids", []))


def _mark_processed(state: dict, owner: Optional[str], ids: List[str]) -> None:
    key = owner or ""
    if key not in state:
        state[key] = {}
    existing = set(state[key].get("processed_experience_ids", []))
    existing.update(ids)
    state[key]["processed_experience_ids"] = sorted(existing)
    state[key]["last_reflection"] = int(time.time())


async def reflect_on_experiences(
    memory_manager,
    memory_vector=None,
    *,
    owner: Optional[str] = None,
    session_id: Optional[str] = None,
) -> int:
    """Scan an owner's experiences and persist learned traits.

    Runs as a background job after each completed response, gated by the same
    privacy switches as experience recording plus the auto_reflection
    preference (default off).

    Returns the number of traits created or revised. Never raises.
    """
    try:
        with memory_write_lock():
            try:
                all_entries = memory_manager.load_all_for_update()
            except MemoryStoreUnreadable as e:
                logger.error("Skipping reflection, store unreadable: %s", e)
                return 0

            # Load sidecar state while holding the same lock so concurrent
            # reflection jobs cannot lose sidecar updates.
            state = _load_reflection_state(memory_manager)

            experiences = [
                e for e in all_entries
                if _is_experience_entry(e)
                and (owner is None or e.get("owner") == owner or e.get("owner") is None)
            ]

            if owner:
                experiences = [
                    e for e in experiences
                    if e.get("owner") == owner or e.get("owner") is None
                ]

            if not experiences:
                return 0

            # Sort newest first, bound the scan.
            experiences.sort(key=_parse_ts, reverse=True)
            experiences = experiences[:_MAX_EXPERIENCES_TO_SCAN]

            # Skip already-processed evidence — never re-process the same records.
            already_processed = _processed_ids(state, owner)
            new_experiences = [
                e for e in experiences
                if e.get("id") not in already_processed
            ]
            if not new_experiences:
                return 0

            # Group new experiences by signal.
            by_signal: Dict[str, List[Dict[str, Any]]] = {}
            for e in new_experiences:
                sig = e.get("signal")
                if sig:
                    by_signal.setdefault(sig, []).append(e)

            # Collect all existing traits for this owner.
            traits = [e for e in all_entries if _is_trait_entry(e)
                      and (owner is None or e.get("owner") == owner or e.get("owner") is None)]

            changed = False
            traits_created_or_revised = 0
            newly_processed_ids: List[str] = []

            for signal, sig_experiences in by_signal.items():
                count = len(sig_experiences)
                if count < _MIN_EVIDENCE_COUNT:
                    # Not enough evidence for a trait — leave unprocessed
                    # so earlier evidence can accumulate with later evidence
                    # on a future reflection run.
                    continue

                # Contradiction check: if opposing signals exist for this owner,
                # reduce confidence rather than suppress entirely — the trait still
                # forms but is marked as contested.
                contested = _has_opposing_signals(new_experiences, signal)

                evidence_ids = _collect_evidence_ids(sig_experiences, signal)
                existing_trait = _find_existing_trait(traits, signal)

                # Confidence is based on cumulative evidence, not just the new
                # batch, so it grows as more experiences accumulate.
                total_count = count
                if existing_trait:
                    total_count = int(existing_trait.get("evidence_count", 0) or 0) + count
                confidence = _confidence_for_count(total_count)
                if contested:
                    confidence *= 0.7

                entry = _build_trait_entry(
                    signal=signal,
                    count=count,
                    evidence_ids=evidence_ids,
                    session_id=session_id,
                    owner=owner,
                    existing_trait=existing_trait,
                )
                entry["confidence"] = confidence
                if contested:
                    entry["contested"] = True

                if existing_trait is None:
                    # Assign a fresh id (add_entry would generate one, but we're
                    # managing the full list ourselves under the lock).
                    entry["id"] = str(uuid.uuid4())
                    all_entries.append(entry)
                    logger.info(
                        "Created trait (signal=%s, owner=%s, confidence=%.2f, evidence=%d)",
                        signal, owner or "-", confidence, count,
                    )
                else:
                    # Replace the existing trait entry in place.
                    for i, e in enumerate(all_entries):
                        if e.get("id") == existing_trait.get("id"):
                            all_entries[i] = entry
                            break
                    logger.info(
                        "Revised trait (signal=%s, owner=%s, confidence=%.2f, evidence=%d)",
                        signal, owner or "-", confidence, count,
                    )
                changed = True
                traits_created_or_revised += 1

                for e in sig_experiences:
                    eid = e.get("id")
                    if eid:
                        newly_processed_ids.append(str(eid))

            if changed or newly_processed_ids:
                _mark_processed(state, owner, newly_processed_ids)
                memory_manager.save(all_entries)
                _save_reflection_state(memory_manager, state)

            return traits_created_or_revised

    except Exception as e:
        # Absolute fail-soft: reflection must never break the chat path.
        logger.error("Reflection failed: %s", e)
        return 0
