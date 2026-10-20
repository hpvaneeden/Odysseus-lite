"""Slice 1: evidence-linked experience recording (evolving social behaviour).

Covers, in order:
- conservative classification — explicit signals accepted; questions, pastes,
  code fences, low-signal pleasantries, and bare swearing rejected, with no
  mood inference from ambiguous language;
- evidence refs built only from verified persisted message ids;
- persistence into the canonical memory.json with owner isolation,
  evidence-scoped same-owner dedup, and the hard per-turn cap;
- fail-safe behaviour on an unreadable store (never overwritten) and
  best-effort vector indexing that is never consulted for dedup;
- the run_post_response_tasks wiring — the gate defaults OFF (the existing
  ["memory", "skill"] job list pinned by test_kv_cache_invalidation_2927 must
  not change) and every privacy switch suppresses the job;
- audit protection: the facts-oriented tidy can neither see, merge, rewrite,
  nor drop category=experience entries.

Modules are loaded directly by path (mirroring
test_memory_extractor_vector_cross_tenant) so importing them never triggers
services/__init__ and its heavy search-stack dependencies.
"""
import asyncio
import importlib.util
import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.memory import MemoryManager

ROOT = Path(__file__).resolve().parents[1]


def _load_module(mod_name: str, rel_path: str):
    spec = importlib.util.spec_from_file_location(mod_name, ROOT / rel_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_recorder():
    return _load_module(
        "experience_recorder_under_test", "services/memory/experience_recorder.py",
    )


def _load_extractor():
    return _load_module(
        "memory_extractor_audit_under_test", "services/memory/memory_extractor.py",
    )


def _msg(role, text, db_id=None):
    return {
        "role": role,
        "content": text,
        "metadata": {"_db_id": db_id} if db_id else {},
    }


def _session(messages, owner="alice", sid="sess-1"):
    return SimpleNamespace(
        owner=owner,
        id=sid,
        get_context_messages=lambda: [dict(m) for m in messages],
    )


# ── Classification: conservative rejection (the default) ────────────────────

_REJECTED = [
    ("", "empty"),
    ("ok", "bare ack"),
    ("thanks", "pleasantry"),
    ("Thanks!", "pleasantry with punctuation"),
    ("hi", "greeting"),
    ("lol", "bare laughter"),
    ("great", "bare backchannel praise"),
    ("well done", "backchannel-shaped praise"),
    ("What's the weather tomorrow?", "routine question"),
    ("What's wrong with this code?", "question containing 'wrong'"),
    ("Does it work now?", "question containing outcome phrasing"),
    ("Summarize this email.", "ordinary work request"),
    ("git status returns an error.", "tool chatter"),
    ("damn", "a swear word alone must not imply hostility"),
    ("what the hell", "swearing alone is not hostility inference"),
    ("I don't like coffee.", "preference statement, not feedback"),
    ("That's funny, the file disappeared.", "ambiguous funny = strange"),
    ("```python\nprint(\"that's perfect\")\n```", "fenced code paste"),
]


@pytest.mark.parametrize("message,reason", _REJECTED)
def test_classify_rejects_low_signal_and_ambiguous_messages(message, reason):
    assert _load_recorder().classify_user_message(message) is None, reason


# ── Classification: explicit signals are accepted ───────────────────────────

_ACCEPTED = [
    # corrections
    ("No, I meant Tuesday.", "correction"),
    ("Correction: the meeting is at 5pm.", "correction"),
    ("That's wrong — the answer is Canberra.", "correction"),
    ("You got it wrong, the file is in /tmp.", "correction"),
    ("That's not what I asked for.", "correction"),
    ("You misunderstood — I asked for a summary.", "correction"),
    ("Actually, it's Thursday.", "correction"),
    ("I meant the other one.", "correction"),
    # negative feedback / boundaries
    ("That was rude.", "negative_feedback"),
    ("I don't like that tone.", "negative_feedback"),
    ("Please stop saying that.", "negative_feedback"),
    ("That's not helpful.", "negative_feedback"),
    ("This was not what I wanted.", "negative_feedback"),
    ("Answers are getting too wordy.", "negative_feedback"),
    ("Be more concise.", "negative_feedback"),
    # outcome acknowledgements
    ("That worked, thanks!", "outcome_ack"),
    ("It works now!", "outcome_ack"),
    ("works now", "outcome_ack"),
    ("Got it running.", "outcome_ack"),
    ("That fixed it.", "outcome_ack"),
    ("Problem solved.", "outcome_ack"),
    ("Exactly what I needed.", "outcome_ack"),
    # positive feedback
    ("That's perfect!", "positive_feedback"),
    ("Well done on fixing that.", "positive_feedback"),
    ("Nice job on the summary.", "positive_feedback"),
    ("You're right about these.", "positive_feedback"),
    ("I loved your explanation.", "positive_feedback"),
    # social signals (humour / emotion)
    ("That's hilarious.", "social_signal"),
    ("You're genuinely funny.", "social_signal"),
    ("I'm so happy!", "social_signal"),
    ("You're a real star.", "social_signal"),
    ("That made me laugh.", "social_signal"),
]


@pytest.mark.parametrize("message,expected", _ACCEPTED)
def test_classify_accepts_explicit_signals(message, expected):
    got = _load_recorder().classify_user_message(message)
    assert got is not None, message
    assert got["signal"] == expected, message
    assert got["description"], "every signal carries a bounded description"


def test_long_pastes_and_buried_praise_are_rejected():
    rec = _load_recorder()
    # pastes are not feedback signals
    assert rec.classify_user_message("x" * 1001) is None
    # praise buried in a long directive is context-dependent: rejected
    buried = (
        "When you summarize the quarterly report please remember to include "
        "file paths, dates, and short justifications for every single "
        "recommendation you make in the summary. That's perfect."
    )
    assert len(buried) > 140
    assert rec.classify_user_message(buried) is None
    # short control: the same phrase as a standalone reply IS feedback
    assert rec.classify_user_message("That's perfect") is not None


def test_long_correction_is_still_accepted():
    # Corrections are unambiguous even in longer messages: no short-length gate.
    long_correction = (
        "By the way, when I asked you to refactor the module I meant the "
        "whole package, not just one file — you got it wrong there and the "
        "tests still fail on import."
    )
    got = _load_recorder().classify_user_message(long_correction)
    assert got is not None and got["signal"] == "correction"


# ── Evidence construction: verified, session-scoped ids only ────────────────

def test_build_record_uses_verified_persisted_ids():
    rec = _load_recorder()
    msgs = [
        _msg("user", "That worked!", "u-9"),
        _msg("assistant", "Glad it helped.", "a-9"),
    ]
    record = rec.build_experience_record(msgs, session_id="sess-7")
    assert record is not None
    assert record["evidence"] == ["sess-7:u-9", "sess-7:a-9"]
    assert record["category"] == "experience"
    assert record["source"] == "experience"
    assert record["session_id"] == "sess-7"
    assert record["signal"] == "outcome_ack"
    assert record["trigger"] == "That worked!"
    assert record["text"].endswith("confirmed the outcome worked.")
    # no identifier fields beyond the sanctioned ones — and never any
    # copied transcript: text is the neutral description, trigger bounded
    assert len(record["text"]) <= 160


def test_build_record_never_invents_identifiers_or_scope():
    rec = _load_recorder()
    # unpersisted user message → no verified id → no record
    no_db_id = [_msg("user", "That worked!")]
    assert rec.build_experience_record(no_db_id, session_id="sess-1") is None
    # persisted but no session scope → no record (evidence must be scoped)
    with_id = [_msg("user", "That worked!", "u-1")]
    assert rec.build_experience_record(with_id, session_id=None) is None
    # empty / assistant-only windows → no record
    assert rec.build_experience_record([], session_id="sess-1") is None
    assert rec.build_experience_record(
        [_msg("assistant", "That worked!", "a-1")], session_id="sess-1",
    ) is None


def test_build_record_keeps_user_ref_when_reply_is_unpersisted():
    rec = _load_recorder()
    msgs = [_msg("user", "That worked!", "u-1"), _msg("assistant", "ok")]
    record = rec.build_experience_record(msgs, session_id="sess-1")
    assert record is not None
    assert record["evidence"] == ["sess-1:u-1"]


def test_build_record_handles_multimodal_content():
    rec = _load_recorder()
    msgs = [{
        "role": "user",
        "content": [{"type": "text", "text": "That's perfect!"}],
        "metadata": {"_db_id": "u-2"},
    }]
    record = rec.build_experience_record(msgs, session_id="sess-1")
    assert record is not None
    assert record["signal"] == "positive_feedback"


def test_build_record_trigger_is_bounded():
    rec = _load_recorder()
    wordy = "That worked " + "and then some " * 20
    record = rec.build_experience_record(
        [_msg("user", wordy, "u-1")], session_id="sess-1",
    )
    assert record is not None
    assert len(record["trigger"]) <= 120


# ── End-to-end recording against the canonical MemoryManager ────────────────

_TURN = [
    _msg("user", "That worked!", "u-1"),
    _msg("assistant", "Glad it helped.", "a-1"),
]


def test_record_persists_owner_scoped_experience(tmp_path):
    rec = _load_recorder()
    mgr = MemoryManager(str(tmp_path))

    n = asyncio.run(rec.record_experiences(
        _session(_TURN), mgr, None, owner="alice", session_id="sess-1",
    ))
    assert n == 1

    rows = mgr.load(owner="alice")
    assert len(rows) == 1
    entry = rows[0]
    assert entry["category"] == "experience"
    assert entry["source"] == "experience"
    assert entry["owner"] == "alice"
    assert entry["session_id"] == "sess-1"
    assert entry["evidence"] == ["sess-1:u-1", "sess-1:a-1"]
    assert entry["signal"] == "outcome_ack"
    assert entry["trigger"] == "That worked!"
    assert entry["text"].endswith("confirmed the outcome worked.")
    # MemoryManager conventions survive (add_entry contract)
    assert isinstance(entry["id"], str) and entry["id"]
    assert isinstance(entry["timestamp"], int)
    assert entry["uses"] == 0

    # owner isolation: bob sees nothing
    assert mgr.load(owner="bob") == []


def test_dedup_is_evidence_scoped_and_same_owner_only(tmp_path):
    rec = _load_recorder()
    mgr = MemoryManager(str(tmp_path))
    # bob already recorded the SAME evidence ids — must not suppress alice
    mgr.save([{
        "id": "bob-1",
        "text": "User confirmed the outcome worked.",
        "owner": "bob",
        "category": "experience",
        "source": "experience",
        "evidence": ["sess-1:u-1", "sess-1:a-1"],
        "session_id": "sess-1",
    }])

    run = lambda owner: asyncio.run(rec.record_experiences(
        _session(_TURN), mgr, None, owner=owner, session_id="sess-1",
    ))
    assert run("alice") == 1, "cross-owner evidence must not suppress recording"
    assert run("bob") == 0, "same owner + same evidence is a duplicate"
    assert run("alice") == 0, "same owner + same evidence is a duplicate"

    assert len(mgr.load(owner="alice")) == 1
    assert len(mgr.load(owner="bob")) == 1  # bob's row untouched


def test_hard_per_turn_cap_records_only_the_final_user_message(tmp_path):
    rec = _load_recorder()
    assert rec.MAX_EXPERIENCES_PER_TURN == 1
    mgr = MemoryManager(str(tmp_path))
    messages = [
        _msg("user", "That worked!", "u0"),
        _msg("assistant", "Great!", "a0"),
        _msg("user", "That's perfect!", "u1"),
        _msg("assistant", "Thanks!", "a1"),
    ]
    n = asyncio.run(rec.record_experiences(
        _session(messages), mgr, None, owner="alice", session_id="sess-1",
    ))
    assert n <= rec.MAX_EXPERIENCES_PER_TURN
    rows = mgr.load(owner="alice")
    assert len(rows) == 1
    # only the final user message of the turn is the event
    assert rows[0]["evidence"] == ["sess-1:u1", "sess-1:a1"]


def test_non_qualifying_turn_records_nothing(tmp_path):
    rec = _load_recorder()
    mgr = MemoryManager(str(tmp_path))
    messages = [
        _msg("user", "What's the weather tomorrow?", "u-1"),
        _msg("assistant", "Sunny.", "a-1"),
    ]
    n = asyncio.run(rec.record_experiences(
        _session(messages), mgr, None, owner="alice", session_id="sess-1",
    ))
    assert n == 0
    assert mgr.load(owner="alice") == []


def test_unpersisted_message_records_nothing(tmp_path):
    rec = _load_recorder()
    mgr = MemoryManager(str(tmp_path))
    # qualifying text but no verified _db_id → nothing recorded, nothing invented
    n = asyncio.run(rec.record_experiences(
        _session([_msg("user", "That worked!")]), mgr, None,
        owner="alice", session_id="sess-1",
    ))
    assert n == 0
    assert mgr.load(owner="alice") == []


# ── Fail-safe storage & best-effort vectors ─────────────────────────────────

def test_unreadable_store_is_reported_never_overwritten(tmp_path):
    """The read-modify-write contract of issue #5673: a store that cannot be
    parsed must be left byte-for-byte untouched — never reconstructed from
    partial data."""
    rec = _load_recorder()
    mgr = MemoryManager(str(tmp_path))
    mgr.save([{
        "id": "m1", "text": "user prefers dark mode",
        "owner": "alice", "category": "preference",
    }])
    with open(mgr.memory_file, "w", encoding="utf-8") as f:
        f.write('[{"id": "m1", "tex')  # truncated mid-write
    with open(mgr.memory_file, "rb") as f:
        before = f.read()

    n = asyncio.run(rec.record_experiences(
        _session(_TURN), mgr, None, owner="alice", session_id="sess-1",
    ))
    assert n == 0, "unreadable store must fail closed, not record"

    with open(mgr.memory_file, "rb") as f:
        after = f.read()
    assert after == before, "an unreadable store must never be overwritten"


class _Vector:
    def __init__(self, healthy=True, raise_on_add=False):
        self.healthy = healthy
        self.raise_on_add = raise_on_add
        self.added = []
        self.find_similar_calls = 0

    def find_similar(self, text, threshold=0.92):
        # The recorder must NEVER consult the vector store for dedup: the
        # shared collection has no owner metadata, so a hit could be another
        # tenant's row (cross-tenant suppression / disclosure).
        self.find_similar_calls += 1
        return "would-be-duplicate"

    def add(self, memory_id, text):
        if self.raise_on_add:
            raise RuntimeError("chroma unavailable")
        self.added.append((memory_id, text))


@pytest.mark.parametrize(
    "vector_kwargs,expect_added",
    [
        ({"healthy": True}, 1),
        ({"healthy": True, "raise_on_add": True}, 0),  # vector failed → swallowed
        ({"healthy": False}, 0),                        # degraded store → skipped
    ],
)
def test_vector_indexing_is_best_effort_and_never_dedups(
    tmp_path, vector_kwargs, expect_added,
):
    rec = _load_recorder()
    mgr = MemoryManager(str(tmp_path))
    vector = _Vector(**vector_kwargs)

    n = asyncio.run(rec.record_experiences(
        _session(_TURN), mgr, vector, owner="alice", session_id="sess-1",
    ))
    assert n == 1, "JSON is the source of truth — vector trouble never blocks recording"
    assert len(mgr.load(owner="alice")) == 1
    assert vector.find_similar_calls == 0, "vector must never be consulted for dedup"
    assert len(vector.added) == expect_added
    if expect_added:
        assert vector.added[0][1].endswith("confirmed the outcome worked.")


# ── Wiring: run_post_response_tasks job registration & gating ───────────────

def _install_chat_helpers(monkeypatch):
    # Mirrors test_kv_cache_invalidation_2927._install_chat_helpers_stubs:
    # stub only modules genuinely absent from sys.modules (conftest already
    # stubs heavy optional deps), then import the real module.
    for mod_name in [
        "starlette.middleware",
        "starlette.middleware.base",
        "core.models",
        "core.database",
        "routes.prefs_routes",
        "routes.research_routes",
        "src.llm_core",
        "src.context_compactor",
        "src.model_context",
        "src.auth_helpers",
    ]:
        if mod_name not in sys.modules:
            monkeypatch.setitem(sys.modules, mod_name, MagicMock())
    return importlib.import_module("routes.chat_helpers")


def _stub_job_modules(monkeypatch):
    # Same unconditional setitem pattern test_kv_cache_invalidation_2927 uses
    # for the lazily-imported job modules. Bare parent packages (only when
    # absent) keep `from services... import` resolvable from sys.modules
    # without ever executing the real heavy services/__init__.
    if "services" not in sys.modules:
        pkg = types.ModuleType("services")
        pkg.__path__ = []
        monkeypatch.setitem(sys.modules, "services", pkg)
    if "services.memory" not in sys.modules:
        sub = types.ModuleType("services.memory")
        sub.__path__ = []
        monkeypatch.setitem(sys.modules, "services.memory", sub)

    rec_mod = types.ModuleType("services.memory.experience_recorder")

    async def fake_record_experiences(*a, **k):
        return 1

    rec_mod.record_experiences = fake_record_experiences
    monkeypatch.setitem(sys.modules, "services.memory.experience_recorder", rec_mod)

    mem_mod = types.ModuleType("services.memory.memory_extractor")

    async def fake_extract_and_store(*a, **k):
        return None

    mem_mod.extract_and_store = fake_extract_and_store
    monkeypatch.setitem(sys.modules, "services.memory.memory_extractor", mem_mod)

    skill_mod = types.ModuleType("services.memory.skill_extractor")

    async def fake_maybe_extract_skill(*a, **k):
        return None

    skill_mod.maybe_extract_skill = fake_maybe_extract_skill
    monkeypatch.setitem(sys.modules, "services.memory.skill_extractor", skill_mod)

    te_mod = types.ModuleType("src.task_endpoint")
    te_mod.resolve_task_endpoint = (
        lambda url, model, headers, owner=None: (url, model, headers)
    )
    monkeypatch.setitem(sys.modules, "src.task_endpoint", te_mod)


def _capture_jobs(monkeypatch, chat_helpers):
    """Replace the sequential runner + spawner with capture-and-close fakes.

    Job REGISTRATION is what these tests pin; the coroutines must never run
    (closing a never-started coroutine is silent and warning-free)."""
    captured = {"session_id": None, "names": []}

    def fake_runner(session_id, jobs):
        captured["session_id"] = session_id
        captured["names"] = [name for name, _ in jobs]
        for _, job in jobs:
            job.close()
        return None

    monkeypatch.setattr(chat_helpers, "_run_extraction_jobs_sequentially", fake_runner)
    monkeypatch.setattr(chat_helpers, "_spawn_bg", lambda coro: None)
    return captured


def _run_post(chat_helpers, uprefs, **kwargs):
    sess = SimpleNamespace(
        endpoint_url="http://localhost:1234/v1",
        model="test-model",
        headers={},
        history=[object()] * 8,
        name="My session title",
    )
    session_manager = SimpleNamespace(save_sessions=lambda: None)
    chat_helpers.run_post_response_tasks(
        sess, session_manager, "sess-Y", "hello", "hi there", None, uprefs,
        memory_manager=MagicMock(), memory_vector=MagicMock(),
        webhook_manager=None,
        agent_rounds=3, agent_tool_calls=3, skills_manager=MagicMock(),
        owner="tester", extract_skills=True,
        **kwargs,
    )


def _wiring_env(monkeypatch):
    chat_helpers = _install_chat_helpers(monkeypatch)
    _stub_job_modules(monkeypatch)
    monkeypatch.setattr(chat_helpers, "needs_auto_name", lambda name: False)
    captured = _capture_jobs(monkeypatch, chat_helpers)
    return chat_helpers, captured


def test_experience_job_is_off_by_default(monkeypatch):
    """Backward-compatibility pin: without the auto_experience pref the job
    list must stay exactly ["memory", "skill"] — the list already pinned by
    test_kv_cache_invalidation_2927."""
    chat_helpers, captured = _wiring_env(monkeypatch)
    _run_post(chat_helpers, {"auto_memory": True, "auto_skills": True})
    assert captured["names"] == ["memory", "skill"]
    assert "experience" not in captured["names"]


def test_experience_job_opt_in_registers_deterministic_job_first(monkeypatch):
    """Opted in, the LLM-free deterministic job is queued BEFORE the LLM
    extraction, so a slow extraction/audit can never delay or starve it."""
    chat_helpers, captured = _wiring_env(monkeypatch)
    _run_post(chat_helpers, {
        "auto_memory": True, "auto_skills": True, "auto_experience": True,
    })
    assert captured["names"] == ["experience", "memory", "skill"]
    assert captured["session_id"] == "sess-Y"


@pytest.mark.parametrize(
    "kwargs,prefs,expected_names",
    [
        # every privacy switch suppresses experience recording:
        ({"incognito": True},
         {"auto_memory": True, "auto_skills": True, "auto_experience": True},
         []),
        ({"compare_mode": True},
         {"auto_memory": True, "auto_skills": True, "auto_experience": True},
         []),
        ({"allow_background_extraction": False},
         {"auto_memory": True, "auto_skills": True, "auto_experience": True},
         []),
        ({"allow_background_extraction": True},
         {"auto_memory": True, "auto_skills": True, "auto_experience": True,
          "memory_enabled": False},
         ["skill"]),
        ({"allow_background_extraction": True},
         {"auto_memory": False, "auto_skills": True, "auto_experience": True},
         ["skill"]),
    ],
)
def test_experience_job_privacy_gates_suppress_registration(
    monkeypatch, kwargs, prefs, expected_names,
):
    chat_helpers, captured = _wiring_env(monkeypatch)
    _run_post(chat_helpers, prefs, **kwargs)
    assert "experience" not in captured["names"]
    assert captured["names"] == expected_names


# ── Audit protection: the facts-oriented tidy cannot touch experiences ──────

_FACTS = [
    {"id": "f1", "text": "User's name is Sam", "owner": "alice", "category": "identity"},
    {"id": "f2", "text": "User likes Python", "owner": "alice", "category": "preference"},
]
_EXPERIENCE = {
    "id": "x1",
    "text": "User confirmed the outcome worked.",
    "owner": "alice",
    "category": "experience",
    "source": "experience",
    "signal": "outcome_ack",
    "trigger": "That worked!",
    "evidence": ["sess-1:u1", "sess-1:a1"],
    "session_id": "sess-1",
}
_FACTS_REPLY = json.dumps([
    {"id": "f1", "text": "User's name is Sam.", "category": "identity"},
    {"id": "f2", "text": "User likes Python.", "category": "preference"},
])


def _seed(rows, tmp_path):
    mgr = MemoryManager(str(tmp_path))
    mgr.save([dict(r) for r in rows])
    return mgr


def _install_llm(monkeypatch, reply):
    """Stub src.llm_core.llm_call_async (same technique as the cross-tenant
    extractor test) while capturing what was sent to the model."""
    mod = types.ModuleType("src.llm_core")
    state = {"calls": 0, "payloads": []}

    async def llm_call_async(endpoint_url, model, messages, **kwargs):
        state["calls"] += 1
        state["payloads"].append(messages)
        if callable(reply):
            return reply(messages)
        return reply

    mod.llm_call_async = llm_call_async
    src_pkg = sys.modules.get("src") or types.ModuleType("src")
    monkeypatch.setitem(sys.modules, "src", src_pkg)
    monkeypatch.setitem(sys.modules, "src.llm_core", mod)
    return state


def test_audit_excludes_experience_from_payload_and_preserves_it(
    tmp_path, monkeypatch,
):
    ext = _load_extractor()
    mgr = _seed(_FACTS + [_EXPERIENCE], tmp_path)
    state = _install_llm(monkeypatch, _FACTS_REPLY)

    result = asyncio.run(ext.audit_memories(
        mgr, None, "http://x", "m", None, owner="alice",
    ))
    assert "error" not in result
    assert result == {"before": 3, "after": 3}

    rows = {r["id"]: r for r in mgr.load(owner="alice")}
    assert set(rows) == {"f1", "f2", "x1"}
    # auditable facts were still processed by the tidy
    assert rows["f1"]["text"] == "User's name is Sam."
    # the experience survived verbatim — id, text, evidence, signal all intact
    assert rows["x1"]["text"] == _EXPERIENCE["text"]
    assert rows["x1"]["evidence"] == _EXPERIENCE["evidence"]
    assert rows["x1"]["signal"] == _EXPERIENCE["signal"]

    # …and it never even reached the model
    user_payload = state["payloads"][0][1]["content"]
    assert "x1" not in user_payload
    assert _EXPERIENCE["text"] not in user_payload


def test_audit_cannot_rewrite_experience_via_model_return(tmp_path, monkeypatch):
    """Even if the model echoes a protected id back with rewritten text, the
    original must survive: protected ids are unknown to the payload-derived
    originals map (skipped as 'unknown id') and re-attached verbatim."""
    ext = _load_extractor()
    mgr = _seed(_FACTS + [_EXPERIENCE], tmp_path)
    reply = json.dumps([
        {"id": "f1", "text": "User's name is Sam.", "category": "identity"},
        {"id": "f2", "text": "User likes Python.", "category": "preference"},
        {"id": "x1", "text": "REWRITTEN BY MODEL", "category": "fact"},
    ])
    _install_llm(monkeypatch, reply)

    result = asyncio.run(ext.audit_memories(
        mgr, None, "http://x", "m", None, owner="alice",
    ))
    assert "error" not in result

    rows = {r["id"]: r for r in mgr.load(owner="alice")}
    assert rows["x1"]["text"] == _EXPERIENCE["text"]
    assert rows["x1"]["evidence"] == _EXPERIENCE["evidence"]


def test_audit_of_experience_only_store_skips_llm(tmp_path, monkeypatch):
    ext = _load_extractor()
    mgr = _seed([_EXPERIENCE], tmp_path)

    def forbidden(messages):
        raise AssertionError("LLM must not be called for protected-only stores")

    state = _install_llm(monkeypatch, forbidden)
    result = asyncio.run(ext.audit_memories(
        mgr, None, "http://x", "m", None, owner="alice",
    ))
    assert result == {"before": 1, "after": 1}
    assert "error" not in result
    assert state["calls"] == 0
    assert {r["id"] for r in mgr.load(owner="alice")} == {"x1"}


def test_experience_additions_do_not_invalidate_tidy_state(tmp_path, monkeypatch):
    """Recording an experience must not re-trigger a facts tidy: the tidy
    fingerprint covers the auditable slice only."""
    ext = _load_extractor()
    mgr = _seed(_FACTS, tmp_path)
    state = _install_llm(monkeypatch, _FACTS_REPLY)

    first = asyncio.run(ext.audit_memories(
        mgr, None, "http://x", "m", None, owner="alice",
    ))
    assert "already_tidy" not in first
    assert state["calls"] == 1

    # a new experience lands between tidy runs
    mgr.save(mgr.load(owner="alice") + [dict(_EXPERIENCE)])

    second = asyncio.run(ext.audit_memories(
        mgr, None, "http://x", "m", None, owner="alice",
    ))
    assert second.get("already_tidy") is True
    assert state["calls"] == 1, "experience must not re-trigger the facts tidy"
    assert {r["id"] for r in mgr.load(owner="alice")} == {"f1", "f2", "x1"}


# ── Regression: experience records must survive concurrent audits ─────────────

def _install_llm_async(monkeypatch, reply_coro_fn):
    """Stub src.llm_core.llm_call_async, properly awaiting async reply functions.

    Mirrors _install_llm but awaits coroutine replies so the stub can run
    concurrent work while the audit/extractor is "awaiting" the LLM.
    """
    mod = types.ModuleType("src.llm_core")
    state = {"calls": 0, "payloads": []}

    async def llm_call_async(endpoint_url, model, messages, **kwargs):
        state["calls"] += 1
        state["payloads"].append(messages)
        return await reply_coro_fn(messages)

    mod.llm_call_async = llm_call_async
    src_pkg = sys.modules.get("src") or types.ModuleType("src")
    monkeypatch.setitem(sys.modules, "src", src_pkg)
    monkeypatch.setitem(sys.modules, "src.llm_core", mod)
    return state


def test_audit_preserves_concurrent_experience_record(tmp_path, monkeypatch):
    """Regression test: an experience record written while an audit is
    awaiting its LLM response must survive the audit's save.

    Interleaving:
      1. Audit loads existing state (phase 1 read).
      2. Audit awaits the LLM (yield point).
      3. Experience recorder writes a new experience (concurrent write).
      4. Audit resumes and saves — must not clobber step 3.
    """
    ext = _load_extractor()
    rec = _load_recorder()
    mgr = _seed(_FACTS, tmp_path)  # f1, f2 owned by alice

    async def run_race():
        audit_at_llm = asyncio.Event()
        release_llm = asyncio.Event()

        async def slow_llm(messages):
            audit_at_llm.set()            # audit has reached the LLM await
            await release_llm.wait()      # block until the test releases it
            return _FACTS_REPLY            # facts-only reply

        _install_llm_async(monkeypatch, slow_llm)

        # Start audit as a concurrent task
        audit_task = asyncio.create_task(
            ext.audit_memories(mgr, None, "http://x", "m", None, owner="alice")
        )

        # Wait until audit is awaiting the LLM
        await audit_at_llm.wait()

        # --- CONCURRENT WRITE: record an experience while audit is awaiting ---
        n = await rec.record_experiences(
            _session(_TURN), mgr, None, owner="alice", session_id="sess-1",
        )
        assert n == 1, "experience recording should succeed during audit await"

        # Release the audit's LLM call
        release_llm.set()

        # Wait for audit to complete
        await audit_task

        # The experience record must still exist — it must not have been
        # clobbered by the audit's stale read-modify-write.
        rows = {r["id"]: r for r in mgr.load(owner="alice")}
        experience_ids = [rid for rid in rows if rid not in {"f1", "f2"}]
        assert len(experience_ids) == 1, (
            f"experience record was lost by the audit; rows={list(rows)}"
        )
        assert rows[experience_ids[0]]["category"] == "experience"

    asyncio.run(run_race())


def test_audit_preserves_concurrent_experience_from_other_session(tmp_path, monkeypatch):
    """Same race, but the experience is recorded for a different session of
    the same owner — the audit must not drop it either."""
    ext = _load_extractor()
    rec = _load_recorder()
    mgr = _seed(_FACTS, tmp_path)

    _TURN_OTHER = [
        _msg("user", "That worked!", "u-2"),
        _msg("assistant", "Glad it helped.", "a-2"),
    ]

    async def run_race():
        audit_at_llm = asyncio.Event()
        release_llm = asyncio.Event()

        async def slow_llm(messages):
            audit_at_llm.set()
            await release_llm.wait()
            return _FACTS_REPLY

        _install_llm_async(monkeypatch, slow_llm)

        audit_task = asyncio.create_task(
            ext.audit_memories(mgr, None, "http://x", "m", None, owner="alice")
        )
        await audit_at_llm.wait()

        n = await rec.record_experiences(
            _session(_TURN_OTHER), mgr, None, owner="alice", session_id="sess-2",
        )
        assert n == 1

        release_llm.set()
        await audit_task

        rows = {r["id"]: r for r in mgr.load(owner="alice")}
        experience_ids = [rid for rid in rows if rid not in {"f1", "f2"}]
        assert len(experience_ids) == 1, (
            f"experience from other session was lost by the audit; rows={list(rows)}"
        )

    asyncio.run(run_race())


# ── Priority 2: owner isolation during deduplication ────────────────────────

def test_dedup_ownerless_request_does_not_scan_other_users(tmp_path):
    """An ownerless recording must not deduplicate against another user's
    private records. Evidence is session-scoped, so same session_id + db_id
    under a different owner is a different event."""
    rec = _load_recorder()
    mgr = MemoryManager(str(tmp_path))

    # Bob already recorded this evidence under his ownership
    mgr.save([{
        "id": "bob-1",
        "text": "User confirmed the outcome worked.",
        "owner": "bob",
        "category": "experience",
        "source": "experience",
        "evidence": ["sess-1:u-1", "sess-1:a-1"],
        "session_id": "sess-1",
    }])

    # Ownerless request (session also has no owner) with the same evidence
    # must NOT be suppressed by bob's row.
    ownerless_session = _session(_TURN, owner=None, sid="sess-1")
    n = asyncio.run(rec.record_experiences(
        ownerless_session, mgr, None, owner=None, session_id="sess-1",
    ))
    assert n == 1, "ownerless recording must not be suppressed by another user's record"

    # Bob's row is untouched
    assert len(mgr.load(owner="bob")) == 1
    # The ownerless record was added
    ownerless = [e for e in mgr.load_all() if e.get("owner") is None]
    assert len(ownerless) == 1


def test_recorder_receives_correct_owner_and_session_id(tmp_path):
    """The owner and session_id passed to record_experiences are stored on the
    entry and used for evidence scoping."""
    rec = _load_recorder()
    mgr = MemoryManager(str(tmp_path))

    n = asyncio.run(rec.record_experiences(
        _session(_TURN), mgr, None, owner="carol", session_id="sess-carol",
    ))
    assert n == 1

    rows = mgr.load(owner="carol")
    assert len(rows) == 1
    entry = rows[0]
    assert entry["owner"] == "carol"
    assert entry["session_id"] == "sess-carol"
    assert entry["evidence"] == ["sess-carol:u-1", "sess-carol:a-1"]

    # Other owners see nothing
    assert mgr.load(owner="dave") == []
    # No ownerless entry was created (owner was explicitly set)
    assert all(e.get("owner") is not None for e in mgr.load_all())


def test_dedup_legacy_ownerless_records_explicit_policy(tmp_path):
    """Legacy ownerless records are included in dedup when an owner is set.

    This is the documented compatibility policy: when owner is set, dedup
    scans own rows plus legacy ownerless rows. This prevents a user from
    re-recording experiences that were previously saved ownerless (before
    ownership was introduced)."""
    rec = _load_recorder()
    mgr = MemoryManager(str(tmp_path))

    # Legacy ownerless experience (pre-ownership)
    mgr.save([{
        "id": "legacy-1",
        "text": "User confirmed the outcome worked.",
        "owner": None,
        "category": "experience",
        "source": "experience",
        "evidence": ["sess-1:u-1", "sess-1:a-1"],
        "session_id": "sess-1",
    }])

    # Alice recording the same evidence should be suppressed by the legacy row
    n = asyncio.run(rec.record_experiences(
        _session(_TURN), mgr, None, owner="alice", session_id="sess-1",
    ))
    assert n == 0, "legacy ownerless record should suppress same-evidence recording"

    # No new row added for alice
    assert len(mgr.load(owner="alice")) == 0
    assert len(mgr.load(owner=None)) == 1  # legacy row untouched


def test_dedup_never_leaks_across_owners(tmp_path):
    """Dedup compares evidence ids only — never text, never owner — so one
    user's experience can never suppress another user's recording of their
    own session's feedback."""
    rec = _load_recorder()
    mgr = MemoryManager(str(tmp_path))

    # Bob's experience with identical description text but different evidence
    mgr.save([{
        "id": "bob-1",
        "text": "User confirmed the outcome worked.",  # same text as alice's would be
        "owner": "bob",
        "category": "experience",
        "source": "experience",
        "evidence": ["sess-bob:u-99", "sess-bob:a-99"],  # different evidence
        "session_id": "sess-bob",
    }])

    # Alice's recording of HER evidence must not be suppressed by bob's row,
    # even though the description text is identical.
    n = asyncio.run(rec.record_experiences(
        _session(_TURN), mgr, None, owner="alice", session_id="sess-1",
    ))
    assert n == 1, "identical text with different evidence must not suppress"

    assert len(mgr.load(owner="alice")) == 1
    assert len(mgr.load(owner="bob")) == 1


# ── Wiring: correct owner and session_id reach the recorder ─────────────────

def test_recorder_receives_correct_owner_and_session_id(tmp_path):
    """The owner and session_id passed to record_experiences are stored on the
    entry and used for evidence scoping."""
    rec = _load_recorder()
    mgr = MemoryManager(str(tmp_path))

    n = asyncio.run(rec.record_experiences(
        _session(_TURN), mgr, None, owner="carol", session_id="sess-carol",
    ))
    assert n == 1

    rows = mgr.load(owner="carol")
    assert len(rows) == 1
    entry = rows[0]
    assert entry["owner"] == "carol"
    assert entry["session_id"] == "sess-carol"
    assert entry["evidence"] == ["sess-carol:u-1", "sess-carol:a-1"]

    # Other owners see nothing
    assert mgr.load(owner="dave") == []
    # No ownerless entry was created (owner was explicitly set)
    assert all(e.get("owner") is not None for e in mgr.load_all())


def test_experience_job_passes_owner_and_session_id(monkeypatch):
    """The run_post_response_tasks wiring passes the effective owner and
    session_id through to record_experiences."""
    chat_helpers = _install_chat_helpers(monkeypatch)
    _stub_job_modules(monkeypatch)

    captured = {"owner": None, "session_id": None, "names": []}

    def fake_record_experiences(session, memory_manager, memory_vector, *,
                                owner=None, session_id=None):
        """Called at coroutine-creation time inside run_post_response_tasks."""
        captured["owner"] = owner
        captured["session_id"] = session_id

        async def _coro():
            return 1
        return _coro()

    rec_mod = types.ModuleType("services.memory.experience_recorder")
    rec_mod.record_experiences = fake_record_experiences
    monkeypatch.setitem(sys.modules, "services.memory.experience_recorder", rec_mod)

    def fake_runner(session_id, jobs):
        captured["names"] = [name for name, _ in jobs]
        for _, job in jobs:
            job.close()
        return None

    monkeypatch.setattr(chat_helpers, "_run_extraction_jobs_sequentially", fake_runner)
    monkeypatch.setattr(chat_helpers, "_spawn_bg", lambda coro: None)
    monkeypatch.setattr(chat_helpers, "needs_auto_name", lambda name: False)

    sess = SimpleNamespace(
        endpoint_url="http://localhost:1234/v1",
        model="test-model",
        headers={},
        history=[object()] * 8,
        name="My session title",
    )
    session_manager = SimpleNamespace(save_sessions=lambda: None)
    chat_helpers.run_post_response_tasks(
        sess, session_manager, "sess-wiring", "hello", "hi there", None,
        {"auto_memory": True, "auto_skills": True, "auto_experience": True},
        memory_manager=MagicMock(), memory_vector=MagicMock(),
        webhook_manager=None,
        agent_rounds=3, agent_tool_calls=3, skills_manager=MagicMock(),
        owner="wiring-owner",
        extract_skills=True,
    )

    assert captured["names"] == ["experience", "memory", "skill"]
    assert captured["owner"] == "wiring-owner"
    assert captured["session_id"] == "sess-wiring"




