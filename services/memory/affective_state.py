"""Separate, owner-scoped affect state for Odysseus and the user.

Odysseus's simulated task affect is updated only from completed tool outcomes.
User affect is recorded only from short, explicit first-person self-reports and
only when its separate preference is enabled. Neither side stores transcript
text; both states decay and live outside the user-authored memory store.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any, Optional

from src.memory import memory_write_lock
from src.prompt_security import untrusted_context_message

logger = logging.getLogger(__name__)

ASSISTANT_HALF_LIFE_SECONDS = 6 * 60 * 60
USER_HALF_LIFE_SECONDS = 8 * 60 * 60
CONTEXT_THRESHOLD = 0.30
MAX_USER_REPORT_CHARS = 400

_EMOTIONS = (
    ("low mood", re.compile(r"\b(?:depress(?:ed|ing)|sad|down|low|miserable)\b", re.I)),
    ("stress", re.compile(r"\b(?:stressed|under stress|overwhelmed)\b", re.I)),
    ("anxiety", re.compile(r"\b(?:anxious|anxiety|worried|nervous)\b", re.I)),
    ("anger", re.compile(r"\b(?:angry|furious|irritated|annoyed)\b", re.I)),
    ("happiness", re.compile(r"\b(?:happy|glad|joyful|pleased)\b", re.I)),
    ("excitement", re.compile(r"\b(?:excited|thrilled|eager)\b", re.I)),
    ("frustration", re.compile(r"\b(?:frustrated|fed\s+up)\b", re.I)),
)
_SELF_REPORT_RE = re.compile(
    r"^\s*(?:i\s+am|i['’]m|im|i\s+feel(?:ing)?|"
    r"i(?:['’]ve|\s+have)\s+been\s+feeling)\s+"
    r"(?P<neg>not\s+)?(?P<rest>(?:[a-z'-]+\s+){0,5}[a-z'-]+)",
    re.I,
)


def _state_path(memory_manager) -> str:
    return str(memory_manager.memory_file) + ".affective_state.json"


def _owner_key(owner: Optional[str]) -> str:
    # The empty key is reserved for the existing auth-disabled single-user
    # deployment. Named owners never fall back to it.
    return str(owner or "")


def _read_state(memory_manager) -> dict[str, Any]:
    try:
        with open(_state_path(memory_manager), "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("owners"), dict):
            return data
        raise ValueError("invalid affective state shape")
    except FileNotFoundError:
        return {"version": 1, "owners": {}}
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"Could not read affective state: {exc}") from exc


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


def _decayed(score: Any, updated_at: Any, now: float, half_life: float) -> float:
    try:
        value = min(1.0, max(0.0, float(score)))
        elapsed = max(0.0, now - float(updated_at))
    except (TypeError, ValueError):
        return 0.0
    value *= 0.5 ** (elapsed / half_life)
    return 0.0 if value < 0.03 else value


def classify_explicit_user_emotion(text: Any) -> Optional[str]:
    """Return a broad, non-diagnostic label only for a direct self-report."""
    if not isinstance(text, str):
        return None
    text = text.strip()
    if (
        not text
        or len(text) > MAX_USER_REPORT_CHARS
        or "```" in text
        or text.count("\n") > 2
        or text.lstrip().startswith((">", "You:", "User:", '"', "'", "“", "‘"))
    ):
        return None
    for report in _SELF_REPORT_RE.finditer(text):
        if report.group("neg"):
            continue
        rest = report.group("rest") or ""
        # Do not mistake a quoted or negated state for the user's current one.
        if re.search(r"\b(?:not|never|no\s+longer|don't|dont|do\s+not)\b", rest, re.I):
            continue
        for label, pattern in _EMOTIONS:
            if pattern.search(rest):
                return label
    return None


def _event_status(event: Any) -> Optional[bool]:
    """Return success/failure for a completed tool event; ignore pending calls."""
    if not isinstance(event, dict):
        return None
    code = event.get("exit_code")
    if code is None:
        return None
    try:
        return int(code) == 0 and not event.get("error")
    except (TypeError, ValueError):
        return None


async def record_assistant_outcome(
    memory_manager,
    *,
    owner: Optional[str],
    tool_events: Any,
) -> bool:
    """Adjust Odysseus's simulated affect from the final completed tool result."""
    if not isinstance(tool_events, list):
        return False
    outcomes = [status for event in tool_events if (status := _event_status(event)) is not None]
    if not outcomes:
        return False
    success = outcomes[-1]
    key = _owner_key(owner)
    now = time.time()
    try:
        with memory_write_lock():
            state = _read_state(memory_manager)
            record = state["owners"].get(key, {})
            assistant = record.get("assistant", {}) if isinstance(record, dict) else {}
            satisfaction = _decayed(
                assistant.get("satisfaction", 0), assistant.get("updated_at", now),
                now, ASSISTANT_HALF_LIFE_SECONDS,
            )
            frustration = _decayed(
                assistant.get("frustration", 0), assistant.get("updated_at", now),
                now, ASSISTANT_HALF_LIFE_SECONDS,
            )
            if success:
                satisfaction = min(1.0, satisfaction + 0.45)
                frustration = max(0.0, frustration - 0.20)
                outcome = "success"
            else:
                frustration = min(1.0, frustration + 0.60)
                satisfaction = max(0.0, satisfaction - 0.20)
                outcome = "failure"
            state["owners"].setdefault(key, {})["assistant"] = {
                "satisfaction": satisfaction,
                "frustration": frustration,
                "last_outcome": outcome,
                "updated_at": now,
            }
            _write_state(memory_manager, state)
        return True
    except Exception:
        logger.warning("Could not record assistant affect state", exc_info=True)
        return False


async def record_user_emotion(
    memory_manager,
    *,
    owner: Optional[str],
    message: Any,
) -> bool:
    """Store only a broad label from an explicit, short first-person report."""
    label = classify_explicit_user_emotion(message)
    if label is None:
        return False
    key = _owner_key(owner)
    try:
        with memory_write_lock():
            state = _read_state(memory_manager)
            state["owners"].setdefault(key, {})["user"] = {
                "emotion": label,
                "strength": 1.0,
                "updated_at": time.time(),
            }
            _write_state(memory_manager, state)
        return True
    except Exception:
        logger.warning("Could not record explicit user affect", exc_info=True)
        return False


def get_affective_context(
    memory_manager,
    *,
    owner: Optional[str],
    assistant_enabled: bool,
    user_recording_enabled: bool,
    now: Optional[float] = None,
) -> list[dict]:
    """Return separate, untrusted prompt notes for active opted-in states."""
    if not assistant_enabled and not user_recording_enabled:
        return []
    try:
        with memory_write_lock():
            state = _read_state(memory_manager)
    except Exception:
        logger.warning("Affective state unavailable; omitting context", exc_info=True)
        return []
    record = state.get("owners", {}).get(_owner_key(owner), {})
    if not isinstance(record, dict):
        return []
    now = time.time() if now is None else now
    result = []

    if assistant_enabled:
        assistant = record.get("assistant", {})
        if isinstance(assistant, dict):
            updated_at = assistant.get("updated_at", now)
            satisfaction = _decayed(
                assistant.get("satisfaction", 0), updated_at, now,
                ASSISTANT_HALF_LIFE_SECONDS,
            )
            frustration = _decayed(
                assistant.get("frustration", 0), updated_at, now,
                ASSISTANT_HALF_LIFE_SECONDS,
            )
            guidance = []
            if frustration >= CONTEXT_THRESHOLD:
                guidance.append(
                    "A recent task failure left your simulated task affect frustrated. "
                    "You may acknowledge frustration about the blocker, without blaming "
                    "the user or dramatizing it, then continue constructively."
                )
            if satisfaction >= CONTEXT_THRESHOLD:
                guidance.append(
                    "A recent task completed successfully and your simulated task affect "
                    "includes satisfaction. You may express modest pleasure in the result; "
                    "do not flatter the user or claim more than the evidence supports."
                )
            if guidance:
                result.append(untrusted_context_message(
                    "Odysseus task affect",
                    "This is your own short-lived, simulated affect, derived only from "
                    "your recorded tool outcomes. It is separate from the user's state; "
                    "never copy the user's emotion into it. Express it only when natural.\n- "
                    + "\n- ".join(guidance),
                ))

    if user_recording_enabled:
        user = record.get("user", {})
        if isinstance(user, dict):
            strength = _decayed(
                user.get("strength", 0), user.get("updated_at", now), now,
                USER_HALF_LIFE_SECONDS,
            )
            emotion = user.get("emotion")
            if strength >= CONTEXT_THRESHOLD and isinstance(emotion, str):
                result.append(untrusted_context_message(
                    "recent explicit user affect",
                    f"The user explicitly described feeling {emotion} recently. This is "
                    "a temporary self-report, not a diagnosis, and may no longer apply. "
                    "Respond to what they say now; acknowledge naturally and let them "
                    "choose whether to talk or be distracted. Do not mirror their mood, "
                    "assume it is relevant to every topic, or infer a task from it.",
                ))
    return result


def reset_affective_state(memory_manager, *, owner: Optional[str]) -> bool:
    """Delete both affect components for exactly one owner."""
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
