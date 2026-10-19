"""
experience_recorder.py

Slice 1 of evolving social behaviour: conservative, evidence-linked recording
of meaningful interaction events into the canonical memory store.

Records experiences as ordinary `memory.json` entries with
``category="experience"`` via the canonical ``MemoryManager`` — no separate
store, no schema change, no migration. Entries carry provenance refs
``"<session_id>:<message _db_id>"`` pointing at messages persisted by
``SessionManager._persist_message``; transcripts are never copied and
identifiers are never invented (an unpersisted message yields no record).

This module is deliberately deterministic: conservative phrase rules, no LLM
call, no mood inference. Only explicit signals are recognised — corrections,
negative feedback/boundaries, outcome acknowledgements, positive feedback,
and short humour/warmth signals. Questions, pastes, code fences, and bare
pleasantries/swearing never match and are rejected by default. A swear word
alone never implies hostility.

Fail-soft by contract: every path returns a count and never raises into the
chat path; an unreadable store is reported, never overwritten. Vector indexing
is best-effort only and is NEVER used for deduplication (the shared collection
has no owner metadata, so vector-based dedup would leak across owners).

Wiring: scheduled as a background job by
``routes.chat_helpers.run_post_response_tasks`` behind the same privacy gates
as memory extraction (incognito / compare / memory_enabled / auto_memory /
allow_background_extraction) plus the ``auto_experience`` preference, which
defaults to off while this layer is under development.

Scope: evidence recording only. Reflection, trait formation, emotional-state
persistence, and any model-visible behaviour are later slices.
"""

import logging
import re
from typing import Any, Dict, List, Optional

from src.memory import MemoryStoreUnreadable

logger = logging.getLogger(__name__)


EXPERIENCE_CATEGORY = "experience"
EXPERIENCE_SOURCE = "experience"

# Hard per-turn record limit. Structural today: only the final user message of
# the turn is classified, so one invocation records at most one entry. Pinned
# as a constant so raising it later is a deliberate, tested decision.
MAX_EXPERIENCES_PER_TURN = 1

# Classification bounds. Pastes and documents are not feedback signals, and
# positive/social phrases buried in long directives are context-dependent —
# so praise and warmth additionally require a short message (see patterns).
_MAX_INPUT_CHARS = 1000
_MAX_SHORT_MSG_CHARS = 140
_MAX_TRIGGER_CHARS = 120
_MAX_DESCRIPTION_CHARS = 160

# How many recent messages are scanned for the turn's final user message.
_CANDIDATE_WINDOW = 6


# ── Descriptions ────────────────────────────────────────────────────────────
# Neutral, factual, bounded. No mood labels: an event record states what the
# user DID, never what they supposedly FELT (mood inference is a later-slice
# problem with its own evidence requirements).

_DESCRIPTIONS = {
    "correction": "User corrected the assistant's response or understanding.",
    "negative_feedback": "User pushed back on the response or set a boundary.",
    "outcome_ack": "User confirmed the outcome worked.",
    "positive_feedback": "User gave explicit positive feedback on the reply.",
    "social_signal": "User signalled humour or positive emotion.",
}


# ── Pattern table ───────────────────────────────────────────────────────────
# (signal, compiled pattern, requires_short_msg)
# Checked in order; first match wins. Patterns are phrase-anchored so routine
# questions ("What's wrong with this code?") and preference statements ("I
# don't like coffee.") never match — only messages that explicitly reference
# the exchange do. Apostrophes are normalised before matching.
_PATTERNS = [
    # ── Correction: the user states the assistant got something wrong ──
    ("correction", re.compile(r"\bcorrections?:", re.I), False),
    ("correction", re.compile(r"\bno\b[.,]?\s+i\s+(?:meant|asked|wanted|said)\b", re.I), False),
    ("correction", re.compile(r"\bi\s+meant\s+(?:not|to|the|a|an|today|tomorrow)\b", re.I), False),
    ("correction", re.compile(r"\byou\s+(?:got|have)\s+(?:it\s+)?wrong\b", re.I), False),
    ("correction", re.compile(r"\bthat(?:'s|\s+is)\s+not\s+what\s+i\s+(?:asked|meant|wanted)\b", re.I), False),
    ("correction", re.compile(r"\bthat(?:'s|\s+is)\s+(?:wrong|incorrect|not\s+right)\b", re.I), False),
    ("correction", re.compile(r"\byou\s+(?:misunderstood|misread|misinterpreted)\b", re.I), False),
    ("correction", re.compile(r"\bactually,?\s+it(?:'s|\s+is)\b", re.I), False),

    # ── Negative feedback / boundaries ──
    ("negative_feedback", re.compile(
        r"\b(?:i\s+do\s+not|don't|dont)\s+(?:like|appreciate)\s+(?:that|this|it|when|how|your)\b", re.I), False),
    ("negative_feedback", re.compile(r"\b(?:please\s+)?stop\s+(?:doing|saying|sending|telling)\b", re.I), False),
    ("negative_feedback", re.compile(
        r"\bthat(?:'s|\s+is|\s+was)\s+(?:rude|unhelpful|not\s+helpful|useless|inappropriate|not\s+funny)\b", re.I), False),
    ("negative_feedback", re.compile(
        r"\b(?:this|that)\s+(?:was\s+)?(?:not|isn't|is\s+not)\s+(?:funny|helpful|what\s+i\s+wanted)\b", re.I), False),
    ("negative_feedback", re.compile(r"\btoo\s+(?:wordy|verbose|long|chatty)\b", re.I), False),
    ("negative_feedback", re.compile(r"\bbe\s+(?:more\s+)?(?:brief|concise|shorter)\b", re.I), False),

    # ── Outcome acknowledgement: the fix/task verifiably worked ──
    ("outcome_ack", re.compile(r"\bthat\s+worked\b", re.I), False),
    ("outcome_ack", re.compile(r"\bworks?\s+now\b", re.I), False),
    ("outcome_ack", re.compile(r"\bgot\s+it\s+(?:working|running|fixed|sorted)\b", re.I), False),
    ("outcome_ack", re.compile(r"\bthat\s+fixed\s+it\b", re.I), False),
    ("outcome_ack", re.compile(r"\b(?:problem|issue)(?:'s|\s+is|\s+was)?\s+(?:now\s+)?solved\b", re.I), False),
    ("outcome_ack", re.compile(r"\bexactly\s+(?:what|how)\s+i\s+(?:needed|wanted)\b", re.I), False),
    ("outcome_ack", re.compile(r"\b(?:glad|happy)\s+it\s+worked\b", re.I), False),

    # ── Positive feedback: must reference the exchange (short messages only) ──
    ("positive_feedback", re.compile(
        r"\b(?:that|this|it)(?:'s|\s+is|\s+was)\s+(?:perfect|excellent|brilliant|great|spot\s+on|exactly\s+right)\b",
        re.I), True),
    ("positive_feedback", re.compile(r"\bwell\s+done\b", re.I), True),
    ("positive_feedback", re.compile(r"\b(?:nice|good)\s+(?:job|work|answer|response|explanation)\b", re.I), True),
    ("positive_feedback", re.compile(
        r"\byou(?:'re|\s+are)\s+(?:always\s+|so\s+|just\s+)?(?:right|correct|the\s+best|a\s+genius|amazing)\b", re.I), True),
    ("positive_feedback", re.compile(r"\bi\s+(?:love(?:d)?|liked|really\s+like)\s+(?:it|this|that|your)\b", re.I), True),

    # ── Social signal: explicit humour appreciation / emotional expression ──
    # "That's funny" is deliberately NOT here — it is ambiguous (humorous vs
    # strange: "That's funny, the file disappeared"). Humour recognition
    # requires the unambiguous forms only.
    ("social_signal", re.compile(r"\b(?:that|this)(?:'s|\s+is)\s+hilarious\b", re.I), True),
    ("social_signal", re.compile(r"\byou(?:'re|\s+are)\s+(?:so\s+|really\s+|genuinely\s+|just\s+|very\s+)?(?:funny|witty)\b", re.I), True),
    ("social_signal", re.compile(r"\bi(?:'m|\s+am)\s+(?:so\s+|really\s+)?(?:happy|glad|thrilled)\b", re.I), True),
    ("social_signal", re.compile(r"\byou(?:'re|\s+are)\s+(?:a\s+|an\s+)?(?:real\s+|absolute\s+)?(?:star|legend|lifesaver)\b", re.I), True),
    ("social_signal", re.compile(r"\bmade\s+(?:me\s+)?(?:laugh|smile|my\s+day)\b", re.I), True),
]

# Full-message (normalised) low-signal pleasantries. Checked before the
# pattern table so backchannel-shaped praise ("well done", "nice job" as a
# bare reflex) is rejected while the same phrases with context still match.
_PLEASANTRIES = frozenset({
    "hi", "hello", "hey", "yo", "sup",
    "thanks", "thank you", "thx", "ty",
    "bye", "goodbye", "good night", "good morning", "good evening",
    "ok", "okay", "k", "kk", "sure", "yes", "yeah", "yep", "no", "nope", "np", "nvm",
    "cool", "nice", "great", "awesome", "perfect", "good", "wonderful", "excellent",
    "well done", "nice job", "good job", "good work", "nice work",
    "lol", "haha", "hahaha", "lmao",
    "you're welcome", "youre welcome", "no problem", "anytime", "my pleasure",
})


def _normalize(text: str) -> str:
    """Apostrophe variants unified, whitespace collapsed. Kept close to the
    original so ``trigger`` stays faithful to what the user typed."""
    text = (text or "").replace("\u2019", "'")
    return " ".join(text.split())


def _message_get(message: Any, key: str, default=None):
    """Dict- and ChatMessage-compatible field access."""
    if isinstance(message, dict):
        return message.get(key, default)
    return getattr(message, key, default)


def _message_text(message: Any) -> str:
    """Plain text of a message; str content or multimodal block lists."""
    content = _message_get(message, "content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                parts.append(str(block.get("text") or ""))
        return " ".join(p for p in parts if p)
    return ""


def _message_db_id(message: Any) -> Optional[str]:
    """The persisted DB id (``metadata._db_id``) or None.

    Set by ``SessionManager._persist_message`` after a successful DB commit —
    verified identifiers only; never synthesised."""
    metadata = _message_get(message, "metadata")
    if not isinstance(metadata, dict):
        return None
    db_id = metadata.get("_db_id")
    if db_id is None or db_id == "":
        return None
    return str(db_id)


def classify_user_message(text: str) -> Optional[Dict[str, str]]:
    """Conservatively classify a user message as a meaningful interaction.

    Returns ``{"signal": ..., "description": ...}`` or ``None``. Rejection is
    the default: routine questions, tool chatter/code pastes, low-signal
    pleasantries, bare swearing, and anything ambiguous never match. Questions
    (trailing ``?``) are never experiences in slice 1 — information requests
    are not feedback, and the patterns are deliberately declarative-only.
    """
    normalized = _normalize(text)
    if len(normalized) < 4:
        return None
    if len(normalized) > _MAX_INPUT_CHARS:
        return None  # pastes/documents are not feedback signals
    if "```" in normalized:
        return None  # fenced code / pasted tool output
    if normalized.endswith("?"):
        return None  # questions are requests, not feedback

    lookup = normalized.casefold().strip(" \t.!?,;:\"'")
    if lookup in _PLEASANTRIES:
        return None

    short_ok = len(normalized) <= _MAX_SHORT_MSG_CHARS
    for signal, pattern, requires_short in _PATTERNS:
        if requires_short and not short_ok:
            continue
        if pattern.search(normalized):
            return {
                "signal": signal,
                "description": _DESCRIPTIONS[signal],
            }
    return None


def build_experience_record(
    candidate_messages: List[Any],
    *,
    session_id: Optional[str],
) -> Optional[Dict[str, Any]]:
    """Build one experience record for the turn, or None.

    Only the FINAL user message of the candidate window is classified — the
    turn being recorded, and structurally at most
    ``MAX_EXPERIENCES_PER_TURN`` records per invocation. Evidence references
    the real persisted ids of that user message and (when present) the reply
    that followed it. Returns None when there is no classifiable signal or no
    verified identifier to anchor provenance to.
    """
    if not isinstance(candidate_messages, list) or not candidate_messages:
        return None
    if not session_id:
        return None  # evidence must be session-scoped; never invent a scope

    window = candidate_messages[-_CANDIDATE_WINDOW:]
    user_idx = None
    for i in range(len(window) - 1, -1, -1):
        if str(_message_get(window[i], "role", "")).lower() == "user":
            user_idx = i
            break
    if user_idx is None:
        return None

    user_msg = window[user_idx]
    verdict = classify_user_message(_message_text(user_msg))
    if not verdict:
        return None

    user_db_id = _message_db_id(user_msg)
    if not user_db_id:
        return None  # unpersisted message: no verified id, no record

    evidence = [f"{session_id}:{user_db_id}"]
    if user_idx + 1 < len(window):
        reply = window[user_idx + 1]
        if str(_message_get(reply, "role", "")).lower() == "assistant":
            reply_db_id = _message_db_id(reply)
            if reply_db_id:
                evidence.append(f"{session_id}:{reply_db_id}")

    trigger = _normalize(_message_text(user_msg))[:_MAX_TRIGGER_CHARS]
    description = verdict["description"][:_MAX_DESCRIPTION_CHARS]

    return {
        "text": description,
        "category": EXPERIENCE_CATEGORY,
        "source": EXPERIENCE_SOURCE,
        "signal": verdict["signal"],
        "trigger": trigger,
        "evidence": evidence,
        "session_id": str(session_id),
    }


def _is_duplicate(record: Dict[str, Any], same_owner_entries: List[Dict]) -> bool:
    """True when this owner already has an experience for the same evidence.

    Dedup is EVIDENCE-scoped and same-owner only. Deliberately NOT text-based:
    descriptions are generic ("User confirmed the outcome worked."), so text
    dedup would freeze the evidence base at one record per signal forever. And
    never vector-based: the shared collection carries no owner metadata, so a
    vector hit could be another tenant's row (the cross-tenant hazard
    documented in memory_extractor) — that would both suppress records and
    disclose across owners. Exact evidence-id overlap only.
    """
    new_evidence = {str(x) for x in (record.get("evidence") or [])}
    if not new_evidence:
        return False
    for entry in same_owner_entries:
        if not isinstance(entry, dict):
            continue
        if entry.get("category") != EXPERIENCE_CATEGORY:
            continue
        existing_ev = {str(x) for x in (entry.get("evidence") or [])}
        if new_evidence & existing_ev:
            return True
    return False


async def record_experiences(
    session,
    memory_manager,
    memory_vector=None,
    *,
    owner: Optional[str] = None,
    session_id: Optional[str] = None,
) -> int:
    """Record at most one experience entry for the completed turn.

    JSON-first (``MemoryManager`` is the source of truth); vector indexing is
    best-effort and never consulted for dedup. Fail-soft: returns 0 on any
    problem, including an unreadable store — which is reported, never
    overwritten, per the ``load_all_for_update`` contract (issue #5673).

    Returns the number of entries recorded (0 or 1). Never raises.
    """
    try:
        messages = session.get_context_messages()
        if not isinstance(messages, list) or not messages:
            return 0

        if owner is None:
            owner = getattr(session, "owner", None)
        if not session_id:
            session_id = getattr(session, "id", None)

        record = build_experience_record(messages, session_id=session_id)
        if not record:
            return 0

        try:
            existing = memory_manager.load_all_for_update()
        except MemoryStoreUnreadable as e:
            logger.error("Skipping experience recording, store unreadable: %s", e)
            return 0

        # Dedup against this owner's rows only — the same predicate the fact
        # extractor uses: own rows plus legacy ownerless rows when owner set.
        if owner:
            same_owner = [
                e for e in existing
                if isinstance(e, dict)
                and (e.get("owner") == owner or e.get("owner") is None)
            ]
        else:
            same_owner = [e for e in existing if isinstance(e, dict)]

        if _is_duplicate(record, same_owner):
            logger.debug(
                "Experience already recorded for evidence %s", record["evidence"],
            )
            return 0

        entry = memory_manager.add_entry(
            record["text"],
            source=EXPERIENCE_SOURCE,
            category=EXPERIENCE_CATEGORY,
            owner=owner,
        )
        for key in ("signal", "trigger", "evidence", "session_id"):
            if record.get(key) is not None:
                entry[key] = record[key]
        existing.append(entry)
        memory_manager.save(existing)

        # Best-effort vector index. Never a gate (JSON is the source of
        # truth) and never consulted for dedup — see _is_duplicate.
        if memory_vector and getattr(memory_vector, "healthy", False):
            try:
                memory_vector.add(entry["id"], record["text"])
            except Exception as e:
                logger.warning(
                    "Experience vector add failed for %s: %s", entry["id"], e,
                )

        try:
            from src.event_bus import fire_event
            fire_event("memory_added", owner)
        except Exception:
            logger.debug("memory_added event dispatch failed", exc_info=True)

        logger.info(
            "Recorded experience (signal=%s, owner=%s, evidence=%s)",
            record["signal"], owner or "-", record["evidence"],
        )
        return 1

    except Exception as e:
        # Absolute fail-soft: recording is a background nicety and must never
        # break the chat path (the job runner also guards — twice by design).
        logger.error("Experience recording failed: %s", e)
        return 0

