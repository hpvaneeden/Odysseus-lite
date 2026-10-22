"""Slice 3 affect layer: assistant task affect (satisfaction/frustration/last_outcome)
and explicit user emotion reports.

Tests cover the decay formula, outcome recording from tool_events, explicit
user-emotion recording, context generation, and reset — all against real
sidecar files written under the shared memory_write_lock.
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

from src.memory import MemoryManager

ROOT = Path(__file__).resolve().parents[1]


def _load_module(mod_name: str, rel_path: str):
    spec = importlib.util.spec_from_file_location(mod_name, ROOT / rel_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_affective():
    return _load_module(
        "affective_state_under_test", "services/memory/affective_state.py",
    )


def _mgr(tmp_path) -> MemoryManager:
    return MemoryManager(str(tmp_path))


def _sidecar(mgr: MemoryManager) -> Path:
    return Path(str(mgr.memory_file) + ".affective_state.json")


def _read_sidecar(mgr: MemoryManager) -> Dict[str, Any]:
    path = _sidecar(mgr)
    if not path.exists():
        return {"version": 1, "owners": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def _owner_state(mgr: MemoryManager, owner: str) -> Dict[str, Any]:
    return _read_sidecar(mgr).get("owners", {}).get(owner, {})


# ── Decay formula ──────────────────────────────────────────────────────────

def _now() -> float:
    return time.time()


@pytest.mark.parametrize("score,updated_at,now,half_life,expected", [
    (1.0, 0.0, 0.0, 6 * 3600, 1.0),                 # no elapsed time
    (1.0, 0.0, 6 * 3600, 6 * 3600, 0.5),             # one half-life
    (1.0, 0.0, 12 * 3600, 6 * 3600, 0.25),           # two half-lives
    (0.0, 0.0, 1000, 6 * 3600, 0.0),                 # zero stays zero
    (1.5, 0.0, 0.0, 6 * 3600, 1.0),                  # clamped above 1
    (-0.5, 0.0, 0.0, 6 * 3600, 0.0),                 # clamped below 0
    ("not-a-number", 0.0, 0.0, 6 * 3600, 0.0),       # invalid score -> 0
    (1.0, "not-a-time", 0.0, 6 * 3600, 0.0),         # invalid updated_at -> 0
])
def test_decayed_formula(score, updated_at, now, half_life, expected):
    aff = _load_affective()
    got = aff._decayed(score, updated_at, now, half_life)
    assert got == pytest.approx(expected, abs=1e-9), (score, updated_at, now, half_life)


def test_decayed_below_threshold_is_zero():
    aff = _load_affective()
    # 0.04 * 0.5 = 0.02 < 0.03 threshold -> 0.0
    assert aff._decayed(0.04, 0.0, 6 * 3600, 6 * 3600) == 0.0
    # exactly at threshold boundary behaves per formula
    got = aff._decayed(0.06, 0.0, 6 * 3600, 6 * 3600)
    assert got == pytest.approx(0.03, abs=1e-9)


# ── record_assistant_outcome ───────────────────────────────────────────────

def test_record_assistant_outcome_ignores_non_list(tmp_path):
    aff = _load_affective()
    mgr = _mgr(tmp_path)
    assert asyncio.run(aff.record_assistant_outcome(
        mgr, owner="alice", tool_events="not a list",
    )) is False
    assert _owner_state(mgr, "alice") == {}


def test_record_assistant_outcome_ignores_events_without_exit_code(tmp_path):
    aff = _load_affective()
    mgr = _mgr(tmp_path)
    events = [
        {"tool": "shell", "output": "ok"},          # no exit_code -> ignored
        {"tool": "bash", "output": "hi", "error": "boom"},  # error, no code -> ignored
    ]
    assert asyncio.run(aff.record_assistant_outcome(
        mgr, owner="alice", tool_events=events,
    )) is False
    assert _owner_state(mgr, "alice") == {}


def test_record_assistant_outcome_success_path(tmp_path):
    aff = _load_affective()
    mgr = _mgr(tmp_path)
    now = _now()
    events = [
        {"exit_code": 1, "error": "failed"},          # earlier failure
        {"exit_code": 0, "output": "done"},           # last -> success
    ]
    assert asyncio.run(aff.record_assistant_outcome(
        mgr, owner="alice", tool_events=events,
    )) is True
    state = _owner_state(mgr, "alice")["assistant"]
    # decayed from 0 over ~0 elapsed -> 0, then +0.45 / -0.20
    assert state["satisfaction"] == pytest.approx(0.45, abs=1e-9)
    assert state["frustration"] == pytest.approx(0.0, abs=1e-9)
    assert state["last_outcome"] == "success"
    assert state["updated_at"] <= now + 1  # stamped near now


def test_record_assistant_outcome_failure_path(tmp_path):
    aff = _load_affective()
    mgr = _mgr(tmp_path)
    events = [{"exit_code": 0, "output": "ok"}, {"exit_code": 1, "error": "no"}]
    assert asyncio.run(aff.record_assistant_outcome(
        mgr, owner="alice", tool_events=events,
    )) is True
    state = _owner_state(mgr, "alice")["assistant"]
    assert state["frustration"] == pytest.approx(0.60, abs=1e-9)
    assert state["satisfaction"] == pytest.approx(0.0, abs=1e-9)
    assert state["last_outcome"] == "failure"


def test_record_assistant_outcome_decays_before_boost(tmp_path):
    aff = _load_affective()
    mgr = _mgr(tmp_path)
    now = _now()
    # seed a prior satisfaction updated 6 hours ago (one half-life)
    side = _sidecar(mgr)
    side.write_text(json.dumps({
        "version": 1, "owners": {
            "alice": {"assistant": {
                "satisfaction": 1.0, "frustration": 0.0,
                "last_outcome": "success", "updated_at": now - 6 * 3600,
            }},
        },
    }), encoding="utf-8")
    events = [{"exit_code": 0, "output": "done"}]
    assert asyncio.run(aff.record_assistant_outcome(
        mgr, owner="alice", tool_events=events,
    )) is True
    state = _owner_state(mgr, "alice")["assistant"]
    # 1.0 decayed over 6h -> 0.5, then +0.45 -> 0.95
    assert state["satisfaction"] == pytest.approx(0.95, abs=1e-6)
    assert state["updated_at"] >= now
    assert state["last_outcome"] == "success"


def test_record_assistant_outcome_owner_isolation(tmp_path):
    aff = _load_affective()
    mgr = _mgr(tmp_path)
    assert asyncio.run(aff.record_assistant_outcome(
        mgr, owner="alice", tool_events=[{"exit_code": 0}],
    )) is True
    assert asyncio.run(aff.record_assistant_outcome(
        mgr, owner="bob", tool_events=[{"exit_code": 1, "error": "x"}],
    )) is True
    alice = _owner_state(mgr, "alice").get("assistant", {})
    bob = _owner_state(mgr, "bob").get("assistant", {})
    assert alice.get("last_outcome") == "success"
    assert bob.get("last_outcome") == "failure"


def test_record_assistant_outcome_empty_list_returns_false(tmp_path):
    aff = _load_affective()
    mgr = _mgr(tmp_path)
    assert asyncio.run(aff.record_assistant_outcome(
        mgr, owner="alice", tool_events=[],
    )) is False


# ── record_user_emotion ────────────────────────────────────────────────────

@pytest.mark.parametrize("message,label", [
    ("I am frustrated", "frustration"),
    ("i'm sad", "low mood"),
    ("i feel anxious", "anxiety"),
    ("I've been feeling happy", "happiness"),
    ("i am stressed", "stress"),
])
def test_record_user_emotion_stores_explicit_report(tmp_path, message, label):
    aff = _load_affective()
    mgr = _mgr(tmp_path)
    assert asyncio.run(aff.record_user_emotion(
        mgr, owner="alice", message=message,
    )) is True
    state = _owner_state(mgr, "alice")
    assert state.get("user", {}).get("emotion") == label
    assert state["user"]["strength"] == 1.0
    assert "updated_at" in state["user"]


@pytest.mark.parametrize("message", [
    "", "ok", "thanks", "What's the weather?", "I don't like coffee.",
    "I am not frustrated", "you are angry", "```code```",
])
def test_record_user_emotion_ignores_non_reports(tmp_path, message):
    aff = _load_affective()
    mgr = _mgr(tmp_path)
    assert asyncio.run(aff.record_user_emotion(
        mgr, owner="alice", message=message,
    )) is False
    assert _owner_state(mgr, "alice").get("user") is None


# ── get_affective_context ──────────────────────────────────────────────────

def test_get_affective_context_returns_none_when_disabled(tmp_path):
    aff = _load_affective()
    mgr = _mgr(tmp_path)
    assert aff.get_affective_context(
        mgr, owner="alice", assistant_enabled=False, user_recording_enabled=False,
    ) == []


def test_get_affective_context_assistant_guidance(tmp_path):
    aff = _load_affective()
    mgr = _mgr(tmp_path)
    side = _sidecar(mgr)
    side.write_text(json.dumps({
        "version": 1, "owners": {
            "alice": {"assistant": {
                "satisfaction": 1.0, "frustration": 0.0,
                "last_outcome": "success", "updated_at": _now(),
            }},
        },
    }), encoding="utf-8")
    ctx = aff.get_affective_context(
        mgr, owner="alice", assistant_enabled=True, user_recording_enabled=False,
    )
    assert any("satisfaction" in c["content"].lower() for c in ctx)


def test_get_affective_context_user_guidance(tmp_path):
    aff = _load_affective()
    mgr = _mgr(tmp_path)
    side = _sidecar(mgr)
    side.write_text(json.dumps({
        "version": 1, "owners": {
            "alice": {"user": {
                "emotion": "anxiety", "strength": 1.0, "updated_at": _now(),
            }},
        },
    }), encoding="utf-8")
    ctx = aff.get_affective_context(
        mgr, owner="alice", assistant_enabled=False, user_recording_enabled=True,
    )
    assert any("anxiety" in c["content"].lower() for c in ctx)


# ── reset_affective_state ──────────────────────────────────────────────────

def test_reset_affective_state_deletes_owner(tmp_path):
    aff = _load_affective()
    mgr = _mgr(tmp_path)
    asyncio.run(aff.record_assistant_outcome(
        mgr, owner="alice", tool_events=[{"exit_code": 0}],
    ))
    assert _owner_state(mgr, "alice") != {}
    assert aff.reset_affective_state(mgr, owner="alice") is True
    assert _owner_state(mgr, "alice") == {}


def test_reset_affective_state_returns_false_when_absent(tmp_path):
    aff = _load_affective()
    mgr = _mgr(tmp_path)
    assert aff.reset_affective_state(mgr, owner="alice") is False
