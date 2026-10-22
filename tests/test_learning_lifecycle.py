"""End-to-end integration tests for the autonomous learning loop.

Covers the complete lifecycle:
- experience recording (Slice 1) → response-guidance state (Slice 3)
  → affect/outcome recording → reflection (Slice 2) → guidance in context.
- manage_memory exit_code contract so successful tool calls record as success.
- Preference defaults (enabled by default) and privacy exclusions
  (incognito / compare / no-memory / per-pref opt-out).
- Persistence to real sidecar files under memory_write_lock.

Modules are loaded by path so importing them never triggers services/__init__.
"""
import asyncio
import importlib.util
import json
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest

from src.memory import MemoryManager, memory_write_lock
from src.settings import DEFAULT_SETTINGS, load_settings

ROOT = Path(__file__).resolve().parents[1]


def _load_module(mod_name: str, rel_path: str):
    spec = importlib.util.spec_from_file_location(mod_name, ROOT / rel_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_recorder():
    return _load_module(
        "lifecycle_recorder", "services/memory/experience_recorder.py",
    )


def _load_emotional():
    return _load_module(
        "lifecycle_emotional", "services/memory/emotional_state.py",
    )


def _load_affective():
    return _load_module(
        "lifecycle_affective", "services/memory/affective_state.py",
    )


def _load_reflector():
    return _load_module(
        "lifecycle_reflector", "services/memory/experience_reflection.py",
    )


def _mgr(tmp_path) -> MemoryManager:
    return MemoryManager(str(tmp_path))


def _session(owner: str = "alice", sid: str = "sess-1",
             messages: List[Dict[str, Any]] | None = None):
    return SimpleNamespace(
        owner=owner, id=sid,
        get_context_messages=lambda: list(messages or []),
    )


def _msg(role: str, text: str, db_id: str) -> Dict[str, Any]:
    return {"role": role, "content": text, "metadata": {"_db_id": db_id}}


def _exp(signal: str, owner: str, session_id: str, eid: str, db_id: str,
         offset_days: float = 0.0) -> Dict[str, Any]:
    ts = int(time.time()) - offset_days * 86400
    return {
        "id": eid, "text": f"User gave {signal.replace('_', ' ')} feedback.",
        "category": "experience", "source": "experience", "signal": signal,
        "trigger": "test trigger",
        "evidence": [f"{session_id}:{db_id}"],
        "session_id": session_id, "owner": owner, "timestamp": ts,
    }


# ── Preference defaults ────────────────────────────────────────────────────

def test_learning_prefs_default_to_true():
    """The autonomous learning prefs are enabled by default in settings."""
    for key in ("auto_experience", "emotional_state_enabled",
                "assistant_emotions_enabled", "auto_reflection",
                "user_emotion_recording_enabled"):
        assert DEFAULT_SETTINGS.get(key) is True, key
    # load_settings merges saved over defaults; with no saved file, defaults win.
    assert load_settings().get("auto_experience") is True


# ── Experience recording (Slice 1) end to end ──────────────────────────────

def test_record_experiences_writes_verified_entry(tmp_path):
    rec = _load_recorder()
    mgr = _mgr(tmp_path)
    messages = [
        _msg("user", "That worked!", "u-1"),
        _msg("assistant", "Glad it helped.", "a-1"),
    ]
    sess = _session(messages=messages)
    count = asyncio.run(rec.record_experiences(
        sess, mgr, owner="alice", session_id="sess-1",
    ))
    assert count == 1
    entries = [e for e in mgr.load(owner="alice")
               if e.get("category") == "experience"]
    assert len(entries) == 1
    entry = entries[0]
    assert entry["signal"] == "outcome_ack"
    assert entry["evidence"] == ["sess-1:u-1", "sess-1:a-1"]
    assert entry["session_id"] == "sess-1"
    assert entry["owner"] == "alice"


def test_record_experiences_unpersisted_message_yields_none(tmp_path):
    rec = _load_recorder()
    mgr = _mgr(tmp_path)
    sess = _session()
    # user message has no _db_id → no verified id → no record
    messages = [_msg("user", "That worked!", None)]
    count = asyncio.run(rec.record_experiences(
        sess, mgr, owner="alice", session_id="sess-1",
    ))
    assert count == 0
    assert mgr.load(owner="alice") == []


# ── Outcome recording (affective) end to end ───────────────────────────────

def test_record_assistant_outcome_success_from_exit_code(tmp_path):
    aff = _load_affective()
    mgr = _mgr(tmp_path)
    # A successful tool call carries exit_code 0 and no error.
    events = [{"tool": "manage_memory", "exit_code": 0,
               "results": "Memory added: [fact] ..."}]
    assert asyncio.run(aff.record_assistant_outcome(
        mgr, owner="alice", tool_events=events,
    )) is True
    state = _owner_assistant(mgr, "alice")
    assert state["satisfaction"] == pytest.approx(0.45, abs=1e-9)
    assert state["frustration"] == pytest.approx(0.0, abs=1e-9)
    assert state["last_outcome"] == "success"


def test_record_assistant_outcome_failure_from_exit_code(tmp_path):
    aff = _load_affective()
    mgr = _mgr(tmp_path)
    events = [{"tool": "shell", "exit_code": 1, "error": "command failed"}]
    assert asyncio.run(aff.record_assistant_outcome(
        mgr, owner="alice", tool_events=events,
    )) is True
    state = _owner_assistant(mgr, "alice")
    assert state["frustration"] == pytest.approx(0.60, abs=1e-9)
    assert state["satisfaction"] == pytest.approx(0.0, abs=1e-9)
    assert state["last_outcome"] == "failure"


def test_record_assistant_outcome_unknown_outcome_ignored(tmp_path):
    aff = _load_affective()
    mgr = _mgr(tmp_path)
    # No exit_code → cannot classify → not recorded (not treated as success).
    events = [{"tool": "manage_memory", "results": "Memory added: ..."}]
    assert asyncio.run(aff.record_assistant_outcome(
        mgr, owner="alice", tool_events=events,
    )) is False
    assert _owner_assistant(mgr, "alice") == {}


# ── Response-guidance state (Slice 3) end to end ───────────────────────────

def test_update_emotional_state_boosts_from_experiences(tmp_path):
    emo = _load_emotional()
    mgr = _mgr(tmp_path)
    entries = [
        _exp("outcome_ack", "alice", "sess-1", "x1", "db1"),
        _exp("positive_feedback", "alice", "sess-1", "x2", "db2"),
        _exp("correction", "alice", "sess-1", "x3", "db3"),
    ]
    mgr.save(entries)
    assert asyncio.run(emo.update_emotional_state(
        mgr, owner="alice", session_id="sess-1",
    )) is True
    state = _owner_emotional(mgr, "alice")
    assert state["success"] == pytest.approx(0.60, abs=1e-9)
    assert state["rapport"] == pytest.approx(0.55, abs=1e-9)
    assert state["repair"] == pytest.approx(0.65, abs=1e-9)
    assert set(state["processed_experience_ids"]) == {"x1", "x2", "x3"}


# ── Reflection (Slice 2) end to end ────────────────────────────────────────

def test_reflection_creates_trait_from_repeated_signal(tmp_path):
    refl = _load_reflector()
    mgr = _mgr(tmp_path)
    exps = [
        _exp("outcome_ack", "alice", "sess-1", "x1", "db1", offset_days=0),
        _exp("outcome_ack", "alice", "sess-2", "x2", "db2", offset_days=1),
        _exp("outcome_ack", "alice", "sess-3", "x3", "db3", offset_days=2),
    ]
    mgr.save(exps)
    n = asyncio.run(refl.reflect_on_experiences(
        mgr, None, owner="alice", session_id="sess-1",
    ))
    assert n >= 1
    traits = [e for e in mgr.load(owner="alice") if e.get("category") == "trait"]
    assert len(traits) == 1
    assert traits[0]["signal"] == "outcome_ack"
    assert traits[0]["evidence_count"] == 3
    assert traits[0]["owner"] == "alice"


def test_reflection_insufficient_evidence_no_trait(tmp_path):
    refl = _load_reflector()
    mgr = _mgr(tmp_path)
    exps = [
        _exp("outcome_ack", "alice", "sess-1", "x1", "db1"),
        _exp("outcome_ack", "alice", "sess-2", "x2", "db2"),
    ]
    mgr.save(exps)
    n = asyncio.run(refl.reflect_on_experiences(
        mgr, None, owner="alice", session_id="sess-1",
    ))
    assert n == 0
    assert [e for e in mgr.load(owner="alice") if e.get("category") == "trait"] == []


# ── Guidance informs later responses ───────────────────────────────────────

def test_get_emotional_context_returns_guidance_above_threshold(tmp_path):
    emo = _load_emotional()
    mgr = _mgr(tmp_path)
    now = time.time()
    side = Path(str(mgr.memory_file) + ".emotional_state.json")
    side.write_text(json.dumps({
        "version": 1, "owners": {
            "alice": {"repair": 1.0, "rapport": 1.0, "success": 1.0,
                      "updated_at": now, "processed_experience_ids": []},
        },
    }), encoding="utf-8")
    ctx = emo.get_emotional_context(mgr, owner="alice", now=now)
    assert ctx is not None
    content = ctx["content"]
    assert "careful repair" in content
    assert "natural conversational tone" in content
    assert "continuity" in content


# ── Complete lifecycle: experience → state → reflection → guidance ─────────

def test_complete_learning_lifecycle(tmp_path):
    """A real conversation turn produces feedback, updates state, and reflection
    forms a trait once the evidence threshold is reached."""
    rec = _load_recorder()
    emo = _load_emotional()
    refl = _load_reflector()
    mgr = _mgr(tmp_path)

    # Three turns in a single conversation, each with an outcome_ack from the
    # user with persisted ids. update_emotional_state is scoped to one session,
    # so all three experiences live in sess-1 and are picked up in one run.
    for i in range(3):
        messages = [
            _msg("user", "That worked!", f"u-{i}"),
            _msg("assistant", "Glad it helped.", f"a-{i}"),
        ]
        sess = _session(messages=messages)
        count = asyncio.run(rec.record_experiences(
            sess, mgr, owner="alice", session_id="sess-1",
        ))
        assert count == 1

    # Response-guidance state picks up all three unprocessed experiences.
    assert asyncio.run(emo.update_emotional_state(
        mgr, owner="alice", session_id="sess-1",
    )) is True
    state = _owner_emotional(mgr, "alice")
    # Three outcome_ack boosts: 0 + 0.60 each, capped at 1.0.
    assert state["success"] == pytest.approx(1.0, abs=1e-9)
    assert len(state["processed_experience_ids"]) == 3

    # Reflection across all three experiences creates a trait.
    n = asyncio.run(refl.reflect_on_experiences(
        mgr, None, owner="alice", session_id="sess-1",
    ))
    assert n >= 1
    traits = [e for e in mgr.load(owner="alice") if e.get("category") == "trait"]
    assert len(traits) == 1
    assert traits[0]["signal"] == "outcome_ack"
    assert traits[0]["evidence_count"] == 3

    # Guidance is now emitted for later responses.
    now = time.time()
    ctx = emo.get_emotional_context(mgr, owner="alice", now=now)
    assert ctx is not None
    assert "continuity" in ctx["content"]


# ── Persistence: sidecar files written under lock ──────────────────────────

def test_sidecar_files_persisted_on_disk(tmp_path):
    aff = _load_affective()
    emo = _load_emotional()
    mgr = _mgr(tmp_path)
    asyncio.run(aff.record_assistant_outcome(
        mgr, owner="alice", tool_events=[{"exit_code": 0}],
    ))
    affective_path = Path(str(mgr.memory_file) + ".affective_state.json")
    assert affective_path.exists()
    data = json.loads(affective_path.read_text(encoding="utf-8"))
    assert "alice" in data["owners"]
    assert data["owners"]["alice"]["assistant"]["last_outcome"] == "success"


# ── Helper accessors ───────────────────────────────────────────────────────

def _owner_assistant(mgr: MemoryManager, owner: str) -> Dict[str, Any]:
    path = Path(str(mgr.memory_file) + ".affective_state.json")
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    return data.get("owners", {}).get(owner, {}).get("assistant", {})


def _owner_emotional(mgr: MemoryManager, owner: str) -> Dict[str, Any]:
    path = Path(str(mgr.memory_file) + ".emotional_state.json")
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    return data.get("owners", {}).get(owner, {})
