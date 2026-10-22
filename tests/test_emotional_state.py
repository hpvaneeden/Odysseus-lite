"""Slice 3 response-guidance state: repair, rapport, success derived from
persisted experience entries, with 30-day decay and idempotent replay.

Tests cover the decay formula, experience-driven boosting, idempotence via
processed_experience_ids, owner/session/evidence gating, context generation,
and reset — all against real sidecar files.
"""
import asyncio
import importlib.util
import json
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from src.memory import MemoryManager

ROOT = Path(__file__).resolve().parents[1]


def _load_module(mod_name: str, rel_path: str):
    spec = importlib.util.spec_from_file_location(mod_name, ROOT / rel_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_emotional():
    return _load_module(
        "emotional_state_under_test", "services/memory/emotional_state.py",
    )


def _mgr(tmp_path) -> MemoryManager:
    return MemoryManager(str(tmp_path))


def _sidecar(mgr: MemoryManager) -> Path:
    return Path(str(mgr.memory_file) + ".emotional_state.json")


def _read_sidecar(mgr: MemoryManager) -> Dict[str, Any]:
    path = _sidecar(mgr)
    if not path.exists():
        return {"version": 1, "owners": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def _owner_state(mgr: MemoryManager, owner: str) -> Dict[str, Any]:
    return _read_sidecar(mgr).get("owners", {}).get(owner, {})


def _now() -> float:
    return time.time()


def _exp(signal: str, owner: str, session_id: str, eid: str,
         db_id: str, offset_days: float = 0.0) -> Dict[str, Any]:
    ts = _now() - offset_days * 86400
    return {
        "id": eid,
        "text": f"User gave {signal.replace('_', ' ')} feedback.",
        "category": "experience",
        "source": "experience",
        "signal": signal,
        "trigger": "test trigger",
        "evidence": [f"{session_id}:{db_id}"],
        "session_id": session_id,
        "owner": owner,
        "timestamp": ts,
    }


# ── Decay formula ──────────────────────────────────────────────────────────

def test_decay_formula_real(tmp_path):
    emo = _load_emotional()
    mgr = _mgr(tmp_path)
    now = _now()
    record = {
        "updated_at": now - 30 * 86400,   # one half-life ago
        "repair": 1.0, "rapport": 1.0, "success": 0.0,
    }
    got = emo._decay(record, now)
    assert got["repair"] == pytest.approx(0.5, abs=1e-9)
    assert got["rapport"] == pytest.approx(0.5, abs=1e-9)
    assert got["success"] == 0.0


def test_decay_below_min_score_is_zero(tmp_path):
    emo = _load_emotional()
    mgr = _mgr(tmp_path)
    now = _now()
    # 0.04 * 0.5 = 0.02 < MIN_SCORE 0.05
    record = {"updated_at": now - 30 * 86400, "repair": 0.04, "rapport": 0.0, "success": 0.0}
    got = emo._decay(record, now)
    assert got["repair"] == 0.0


def test_decay_missing_updated_at_uses_now(tmp_path):
    emo = _load_emotional()
    mgr = _mgr(tmp_path)
    now = _now()
    # missing updated_at -> defaults to now -> elapsed 0 -> no decay
    record = {"repair": 1.0, "rapport": 0.0, "success": 0.0}
    got = emo._decay(record, now)
    assert got["repair"] == pytest.approx(1.0, abs=1e-9)


def test_decay_invalid_updated_at_uses_max_age(tmp_path):
    emo = _load_emotional()
    mgr = _mgr(tmp_path)
    now = _now()
    # non-numeric updated_at -> elapsed = EVIDENCE_MAX_AGE_SECONDS (90 days)
    # 1.0 * 0.5^(90/30) = 1.0 * 0.125 = 0.125
    record = {"updated_at": "not-a-time", "repair": 1.0, "rapport": 0.0, "success": 0.0}
    got = emo._decay(record, now)
    assert got["repair"] == pytest.approx(0.125, abs=1e-9)


# ── update_emotional_state ─────────────────────────────────────────────────

def test_update_emotional_state_requires_session_id(tmp_path):
    emo = _load_emotional()
    mgr = _mgr(tmp_path)
    assert asyncio.run(emo.update_emotional_state(mgr, owner="alice", session_id="")) is False
    assert asyncio.run(emo.update_emotional_state(mgr, owner="alice", session_id=None)) is False


def test_update_emotional_state_no_candidates_returns_false(tmp_path):
    emo = _load_emotional()
    mgr = _mgr(tmp_path)
    mgr.save([])  # empty store
    assert asyncio.run(emo.update_emotional_state(mgr, owner="alice", session_id="sess-1")) is False


def test_update_emotional_state_applies_boosts(tmp_path):
    emo = _load_emotional()
    mgr = _mgr(tmp_path)
    now = _now()
    entries = [
        _exp("outcome_ack", "alice", "sess-1", "x1", "db1", offset_days=0),
        _exp("positive_feedback", "alice", "sess-1", "x2", "db2", offset_days=0),
        _exp("correction", "alice", "sess-1", "x3", "db3", offset_days=0),
    ]
    mgr.save(entries)
    assert asyncio.run(emo.update_emotional_state(mgr, owner="alice", session_id="sess-1")) is True
    state = _owner_state(mgr, "alice")
    # fresh decay ~0, then boosts: success +0.60, rapport +0.55, repair +0.65
    assert state["success"] == pytest.approx(0.60, abs=1e-9)
    assert state["rapport"] == pytest.approx(0.55, abs=1e-9)
    assert state["repair"] == pytest.approx(0.65, abs=1e-9)
    assert state["updated_at"] >= now
    assert "x1" in state["processed_experience_ids"]
    assert "x2" in state["processed_experience_ids"]
    assert "x3" in state["processed_experience_ids"]


def test_update_emotional_state_caps_at_one(tmp_path):
    emo = _load_emotional()
    mgr = _mgr(tmp_path)
    # seed rapport already near 1
    side = _sidecar(mgr)
    side.write_text(json.dumps({
        "version": 1, "owners": {
            "alice": {"rapport": 0.9, "repair": 0.0, "success": 0.0,
                      "updated_at": _now(), "processed_experience_ids": []},
        },
    }), encoding="utf-8")
    entries = [_exp("positive_feedback", "alice", "sess-1", "x1", "db1", offset_days=0)]
    mgr.save(entries)
    assert asyncio.run(emo.update_emotional_state(mgr, owner="alice", session_id="sess-1")) is True
    state = _owner_state(mgr, "alice")
    # 0.9 decayed ~0.9 + 0.55 = 1.45 -> capped at 1.0
    assert state["rapport"] == pytest.approx(1.0, abs=1e-9)


def test_update_emotional_state_idempotent_replay(tmp_path):
    emo = _load_emotional()
    mgr = _mgr(tmp_path)
    entries = [_exp("outcome_ack", "alice", "sess-1", "x1", "db1", offset_days=0)]
    mgr.save(entries)
    assert asyncio.run(emo.update_emotional_state(mgr, owner="alice", session_id="sess-1")) is True
    first = dict(_owner_state(mgr, "alice"))
    # second run: x1 already processed -> no candidates -> returns False, no rewrite
    assert asyncio.run(emo.update_emotional_state(mgr, owner="alice", session_id="sess-1")) is False
    assert _owner_state(mgr, "alice") == first


def test_update_emotional_state_owner_and_session_gating(tmp_path):
    emo = _load_emotional()
    mgr = _mgr(tmp_path)
    # alice's experience in sess-1 should not apply to bob or to a different session
    entries = [_exp("outcome_ack", "alice", "sess-1", "x1", "db1", offset_days=0)]
    mgr.save(entries)
    assert asyncio.run(emo.update_emotional_state(mgr, owner="bob", session_id="sess-1")) is False
    assert _owner_state(mgr, "bob") == {}
    assert asyncio.run(emo.update_emotional_state(mgr, owner="alice", session_id="sess-2")) is False
    assert _owner_state(mgr, "alice") == {}


def test_update_emotional_state_requires_evidence_ref(tmp_path):
    emo = _load_emotional()
    mgr = _mgr(tmp_path)
    # experience with evidence that does NOT reference the session -> not eligible
    bad = dict(_exp("outcome_ack", "alice", "sess-1", "x1", "db1", offset_days=0))
    bad["evidence"] = ["other-session:db1"]
    mgr.save([bad])
    assert asyncio.run(emo.update_emotional_state(mgr, owner="alice", session_id="sess-1")) is False


def test_update_emotional_state_respects_max_age(tmp_path):
    emo = _load_emotional()
    mgr = _mgr(tmp_path)
    # experience older than 90 days -> not eligible
    entries = [_exp("outcome_ack", "alice", "sess-1", "x1", "db1", offset_days=100)]
    mgr.save(entries)
    assert asyncio.run(emo.update_emotional_state(mgr, owner="alice", session_id="sess-1")) is False


# ── get_emotional_context ──────────────────────────────────────────────────

def test_get_emotional_context_guidance_thresholds(tmp_path):
    emo = _load_emotional()
    mgr = _mgr(tmp_path)
    now = _now()
    side = _sidecar(mgr)
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


def test_get_emotional_context_below_threshold_returns_none(tmp_path):
    emo = _load_emotional()
    mgr = _mgr(tmp_path)
    now = _now()
    side = _sidecar(mgr)
    side.write_text(json.dumps({
        "version": 1, "owners": {
            "alice": {"repair": 0.1, "rapport": 0.1, "success": 0.1,
                      "updated_at": now, "processed_experience_ids": []},
        },
    }), encoding="utf-8")
    assert emo.get_emotional_context(mgr, owner="alice", now=now) is None


# ── reset_emotional_state ──────────────────────────────────────────────────

def test_reset_emotional_state_deletes_owner(tmp_path):
    emo = _load_emotional()
    mgr = _mgr(tmp_path)
    side = _sidecar(mgr)
    side.write_text(json.dumps({
        "version": 1, "owners": {
            "alice": {"repair": 0.5, "rapport": 0.0, "success": 0.0,
                      "updated_at": _now(), "processed_experience_ids": []},
            "bob": {"repair": 0.2, "rapport": 0.0, "success": 0.0,
                    "updated_at": _now(), "processed_experience_ids": []},
        },
    }), encoding="utf-8")
    assert emo.reset_emotional_state(mgr, owner="alice") is True
    state = _read_sidecar(mgr)
    assert "alice" not in state["owners"]
    assert "bob" in state["owners"]


def test_reset_emotional_state_returns_false_when_absent(tmp_path):
    emo = _load_emotional()
    mgr = _mgr(tmp_path)
    assert emo.reset_emotional_state(mgr, owner="alice") is False
