# src/observability.py
"""
TurnTrace observability layer for Odysseus.

Captures model inputs, state snapshots, memory operations, and self-definition
events for debugging and auditing the harness/model boundary.

All traces are written locally as JSONL files under ``OBSERVABILITY_DIR``
(default: ``<DATA_DIR>/observability/``).  No remote logging service is used
and no trace content is ever transmitted outside the local environment.

Sensitive-data protection
-------------------------
- API keys, auth headers, credentials and transport secrets are never recorded.
- Model-visible content (system prompts, conversation history, recalled
  memories) IS recorded verbatim because it is needed for faithful debugging.
  Operators must treat the observability directory as containing sensitive
  user data and apply appropriate file-system access controls.
- A retention policy (``OBSERVABILITY_RETENTION_DAYS``) controls automatic
  cleanup of old log files.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src import settings
from src.constants import DATA_DIR

import logging

logger = logging.getLogger(__name__)

# ── Configuration ──────────────────────────────────────────────────────────

OBSERVABILITY_DIR = os.path.join(DATA_DIR, "observability")

DEFAULT_RETENTION_DAYS = 30


def _retention_days() -> int:
    """Read retention period from settings or environment."""
    try:
        val = settings.get_setting("observability_retention_days")
        if val is not None:
            return int(val)
    except Exception:
        pass
    env_val = os.getenv("OBSERVABILITY_RETENTION_DAYS")
    if env_val is not None:
        try:
            return int(env_val)
        except ValueError:
            pass
    return DEFAULT_RETENTION_DAYS


DEFAULT_MOOD_STRUCTURE: Dict[str, Any] = {
    "valence": 0.0,
    "arousal": 0.0,
    "dominant_emotion": "neutral",
    "timestamp": None,
    "source": "initial",
}

DEFAULT_PERSONALITY_STRUCTURE: Dict[str, Any] = {
    "openness": 0.5,
    "conscientiousness": 0.5,
    "extraversion": 0.5,
    "agreeableness": 0.5,
    "neuroticism": 0.5,
    "version": 1,
    "last_updated": None,
}

_lock = threading.Lock()


def _ensure_dir() -> None:
    try:
        os.makedirs(OBSERVABILITY_DIR, exist_ok=True)
    except Exception as exc:
        logger.warning("Could not create observability directory %s: %s", OBSERVABILITY_DIR, exc)


def _log_file(event_type: str) -> str:
    return os.path.join(OBSERVABILITY_DIR, f"{event_type}.jsonl")


def _append_jsonl(event_type: str, entry: Dict[str, Any]) -> None:
    try:
        _ensure_dir()
        path = _log_file(event_type)
        line = json.dumps(entry, default=str, ensure_ascii=False)
        with _lock:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
    except Exception as exc:
        logger.warning("Observability write failed for %s: %s", event_type, exc)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _redact_sensitive(obj: Any) -> Any:
    """Redact known sensitive fields from a dict before logging."""
    if isinstance(obj, dict):
        redacted: Dict[str, Any] = {}
        for k, v in obj.items():
            kl = k.lower()
            if any(
                secret in kl
                for secret in (
                    "api_key", "apikey", "secret", "authorization",
                    "bearer", "password", "credential", "token",
                )
            ):
                redacted[k] = "[REDACTED]"
            else:
                redacted[k] = _redact_sensitive(v)
        return redacted
    if isinstance(obj, list):
        return [_redact_sensitive(v) for v in obj]
    return obj


def make_model_input_entry(
    model_input: Dict[str, Any],
    turn_id: str,
    session_id: str,
    correlation_id: Optional[str] = None,
    model: Optional[str] = None,
    endpoint: Optional[str] = None,
    generation_params: Optional[Dict[str, Any]] = None,
    tool_schemas: Optional[List[Dict[str, Any]]] = None,
    captured_at: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "event_type": "model_input",
        "timestamp": captured_at or _now_iso(),
        "session_id": session_id,
        "turn_id": turn_id,
        "correlation_id": correlation_id or turn_id,
        "model": model,
        "endpoint": endpoint,
        "generation_params": generation_params or {},
        "model_input": _redact_sensitive(model_input),
        "tool_schemas": _redact_sensitive(tool_schemas or []),
    }


def make_state_snapshot_entry(
    state: Dict[str, Any],
    turn_id: str,
    session_id: str,
    metadata: Optional[Dict[str, Any]] = None,
    correlation_id: Optional[str] = None,
    captured_at: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "event_type": "state_snapshot",
        "timestamp": captured_at or _now_iso(),
        "session_id": session_id,
        "turn_id": turn_id,
        "correlation_id": correlation_id or turn_id,
        "state": state,
        "metadata": metadata or {},
    }


def make_memory_audit_entry(
    operation: str,
    actor: str,
    turn_id: str,
    session_id: str,
    memory_id: Optional[str] = None,
    category: Optional[str] = None,
    outcome: str = "success",
    reason: Optional[str] = None,
    before: Optional[Dict[str, Any]] = None,
    after: Optional[Dict[str, Any]] = None,
    correlation_id: Optional[str] = None,
    captured_at: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "event_type": "memory_audit",
        "timestamp": captured_at or _now_iso(),
        "session_id": session_id,
        "turn_id": turn_id,
        "correlation_id": correlation_id or turn_id,
        "operation": operation,
        "actor": actor,
        "memory_id": memory_id,
        "category": category,
        "outcome": outcome,
        "reason": reason,
        "before": before,
        "after": after,
    }


def make_naming_event_entry(
    gate_permitted: bool,
    decision_attempted: bool,
    name_selected: Optional[str],
    decision_deferred: bool,
    existing_name_preserved: bool,
    user_override: bool,
    persistence_outcome: str,
    turn_id: str,
    session_id: str,
    evidence_ref: Optional[str] = None,
    source: str = "harness",
    correlation_id: Optional[str] = None,
    captured_at: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "event_type": "naming_event",
        "timestamp": captured_at or _now_iso(),
        "session_id": session_id,
        "turn_id": turn_id,
        "correlation_id": correlation_id or turn_id,
        "gate_permitted": gate_permitted,
        "decision_attempted": decision_attempted,
        "name_selected": name_selected,
        "decision_deferred": decision_deferred,
        "existing_name_preserved": existing_name_preserved,
        "user_override": user_override,
        "persistence_outcome": persistence_outcome,
        "evidence_ref": evidence_ref,
        "source": source,
    }


def make_self_def_event_entry(
    event_type_name: str,
    gate_permitted: bool,
    action_taken: bool,
    outcome: str,
    turn_id: str,
    session_id: str,
    evidence_ref: Optional[str] = None,
    source: str = "harness",
    correlation_id: Optional[str] = None,
    captured_at: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "event_type": "self_def_event",
        "timestamp": captured_at or _now_iso(),
        "session_id": session_id,
        "turn_id": turn_id,
        "correlation_id": correlation_id or turn_id,
        "self_def_event": event_type_name,
        "gate_permitted": gate_permitted,
        "action_taken": action_taken,
        "outcome": outcome,
        "evidence_ref": evidence_ref,
        "source": source,
    }
def write_model_input_trace(entry: Dict[str, Any]) -> None:
    _append_jsonl("model_input", entry)


def write_state_snapshot(entry: Dict[str, Any]) -> None:
    _append_jsonl("state_snapshot", entry)


def write_memory_audit(entry: Dict[str, Any]) -> None:
    _append_jsonl("memory_audit", entry)


def write_naming_event(entry: Dict[str, Any]) -> None:
    _append_jsonl("naming_event", entry)


def write_self_def_event(entry: Dict[str, Any]) -> None:
    _append_jsonl("self_def_event", entry)


def _apply_retention() -> None:
    try:
        retention = _retention_days()
        if retention <= 0:
            return
        cutoff = time.time() - (retention * 86400)
        _ensure_dir()
        for fname in os.listdir(OBSERVABILITY_DIR):
            fpath = os.path.join(OBSERVABILITY_DIR, fname)
            if not os.path.isfile(fpath):
                continue
            try:
                if os.path.getmtime(fpath) < cutoff:
                    os.remove(fpath)
            except OSError:
                pass
    except Exception as exc:
        logger.warning("Observability retention cleanup failed: %s", exc)


class TurnTrace:
    def __init__(
        self,
        session_id: str,
        turn_id: str,
        correlation_id: Optional[str] = None,
    ):
        self.session_id = session_id
        self.turn_id = turn_id
        self.correlation_id = correlation_id or turn_id
        self.model_input: Optional[Dict[str, Any]] = None
        self.before_state: Optional[Dict[str, Any]] = None
        self.after_state: Optional[Dict[str, Any]] = None
        self.memory_operations: List[Dict[str, Any]] = []
        self.naming_events: List[Dict[str, Any]] = []
        self.self_def_events: List[Dict[str, Any]] = []
        self._capture_errors: List[str] = []

    def __enter__(self) -> "TurnTrace":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if exc_type is not None:
            self._capture_errors.append(f"{exc_type.__name__}: {exc_val}")

        if self.model_input is not None:
            entry = dict(self.model_input)
            entry.setdefault("session_id", self.session_id)
            entry.setdefault("turn_id", self.turn_id)
            entry.setdefault("correlation_id", self.correlation_id)
            entry.setdefault("timestamp", _now_iso())
            if self._capture_errors:
                entry["capture_errors"] = self._capture_errors
            write_model_input_trace(entry)

        if self.before_state is not None:
            entry = dict(self.before_state)
            entry.setdefault("session_id", self.session_id)
            entry.setdefault("turn_id", self.turn_id)
            entry.setdefault("correlation_id", self.correlation_id)
            entry.setdefault("timestamp", _now_iso())
            if self._capture_errors:
                entry["capture_errors"] = self._capture_errors
            write_state_snapshot(entry)

        if self.after_state is not None:
            entry = dict(self.after_state)
            entry.setdefault("session_id", self.session_id)
            entry.setdefault("turn_id", self.turn_id)
            entry.setdefault("correlation_id", self.correlation_id)
            entry.setdefault("timestamp", _now_iso())
            if self._capture_errors:
                entry["capture_errors"] = self._capture_errors
            write_state_snapshot(entry)

        for entry in self.memory_operations:
            entry.setdefault("session_id", self.session_id)
            entry.setdefault("turn_id", self.turn_id)
            entry.setdefault("correlation_id", self.correlation_id)
            entry.setdefault("timestamp", _now_iso())
            write_memory_audit(entry)

        for entry in self.naming_events:
            entry.setdefault("session_id", self.session_id)
            entry.setdefault("turn_id", self.turn_id)
            entry.setdefault("correlation_id", self.correlation_id)
            entry.setdefault("timestamp", _now_iso())
            write_naming_event(entry)

        for entry in self.self_def_events:
            entry.setdefault("session_id", self.session_id)
            entry.setdefault("turn_id", self.turn_id)
            entry.setdefault("correlation_id", self.correlation_id)
            entry.setdefault("timestamp", _now_iso())
            write_self_def_event(entry)

        _apply_retention()

    def record_memory(
        self,
        operation: str,
        actor: str,
        memory_id: Optional[str] = None,
        category: Optional[str] = None,
        outcome: str = "success",
        reason: Optional[str] = None,
        before: Optional[Dict[str, Any]] = None,
        after: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.memory_operations.append(
            make_memory_audit_entry(
                operation=operation,
                actor=actor,
                turn_id=self.turn_id,
                session_id=self.session_id,
                memory_id=memory_id,
                category=category,
                outcome=outcome,
                reason=reason,
                before=before,
                after=after,
                correlation_id=self.correlation_id,
            )
        )

    def record_naming(
        self,
        gate_permitted: bool,
        decision_attempted: bool,
        name_selected: Optional[str] = None,
        decision_deferred: bool = False,
        existing_name_preserved: bool = False,
        user_override: bool = False,
        persistence_outcome: str = "not_attempted",
        evidence_ref: Optional[str] = None,
        source: str = "harness",
    ) -> None:
        self.naming_events.append(
            make_naming_event_entry(
                gate_permitted=gate_permitted,
                decision_attempted=decision_attempted,
                name_selected=name_selected,
                decision_deferred=decision_deferred,
                existing_name_preserved=existing_name_preserved,
                user_override=user_override,
                persistence_outcome=persistence_outcome,
                turn_id=self.turn_id,
                session_id=self.session_id,
                evidence_ref=evidence_ref,
                source=source,
                correlation_id=self.correlation_id,
            )
        )

    def record_self_def(
        self,
        event_type_name: str,
        gate_permitted: bool,
        action_taken: bool,
        outcome: str,
        evidence_ref: Optional[str] = None,
        source: str = "harness",
    ) -> None:
        self.self_def_events.append(
            make_self_def_event_entry(
                event_type_name=event_type_name,
                gate_permitted=gate_permitted,
                action_taken=action_taken,
                outcome=outcome,
                turn_id=self.turn_id,
                session_id=self.session_id,
                evidence_ref=evidence_ref,
                source=source,
                correlation_id=self.correlation_id,
            )
        )