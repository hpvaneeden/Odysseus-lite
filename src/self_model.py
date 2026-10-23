"""
Odysseus self-model: a persistent, evidence-based self-description.

Slice 2 of the self-definition capability. Describes what Odysseus is, what it
can actually do, its limitations, its operating principles, and what it may
develop in the future -- grounded in inspected implementation and live
configuration, never in invented personality, feelings, or history.

Design principles
-----------------
- Single structured store (data/self_model.json), not a second general-purpose
  memory system and not memory.json. Memory, reflection, and experience
  recording are left completely untouched.
- Claims are split into three mutually-exclusive categories:
      verified        - supported by inspected implementation / live config
      hypothesis      - plausible but unverified; uncertainty recorded
      aspiration      - desired future capability, not yet established
- Every claim carries provenance: claim text, area, evidence, source type,
  verification status, confidence, and timestamp.
- Master-gated by settings.self_definition_enabled(). When the master switch
  is OFF: no create, update, regenerate, or delete; existing self-model data
  is preserved. When re-ON: existing data remains and is updated conservatively.
- Conservative persistence: the store is rewritten only when the rendered
  model materially changes, so unchanged evidence never triggers a rewrite.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import os
import time
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple

from src import settings
from src.constants import APP_VERSION, DATA_DIR, SELF_MODEL_FILE

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

CAT_VERIFIED = "verified"
CAT_HYPOTHESIS = "hypothesis"
CAT_ASPIRATION = "aspiration"
CATEGORIES = (CAT_VERIFIED, CAT_HYPOTHESIS, CAT_ASPIRATION)

SOURCE_IMPLEMENTATION = "implementation"
SOURCE_CONFIG = "config"
SOURCE_DOCUMENTATION = "documentation"
SOURCE_EXECUTION = "execution"
SOURCE_INTENT = "intent"

STATUS_VERIFIED = "verified"
STATUS_UNVERIFIED = "unverified"
STATUS_ASPIRATIONAL = "aspirational"

AREA_ROLE = "role"
AREA_CAPABILITY = "capability"
AREA_TOOL = "tool"
AREA_LIMITATION = "limitation"
AREA_PRINCIPLE = "principle"
AREA_SECURITY = "security"
AREA_QUESTION = "question"
AREA_ASPIRATION = "aspiration"

# Autonomous naming state is stored at the top level of the self-model,
# alongside ``categories``. ``source`` distinguishes an autonomously chosen
# name from a user-specified override.
NAMING_KEY = "autonomous_naming"
NAMING_SOURCE_AUTONOMOUS = "autonomous"
NAMING_SOURCE_USER = "user"


def _claim(
    claim: str,
    area: str,
    evidence: str,
    source_type: str,
    verification_status: str,
    confidence: float,
) -> Dict[str, Any]:
    """Build a single provenance-carrying claim entry."""
    return {
        "id": str(uuid.uuid4()),
        "claim": claim,
        "area": area,
        "evidence": evidence,
        "source_type": source_type,
        "verification_status": verification_status,
        "confidence": float(confidence),
        "timestamp": int(time.time()),
    }


def _read_tool_schema_names() -> Tuple[List[str], bool]:
    """Return the registered function-tool schema names.

    Tool *registration* is verified here; successful *execution* is NOT.
    Returns (names, introspection_ok). On any failure the names list is empty
    and introspection_ok is False -- callers must not fabricate execution
    claims from a missing registry.
    """
    try:
        from src.tool_schemas import FUNCTION_TOOL_SCHEMAS  # lazy; heavy deps

        names: List[str] = []
        for entry in FUNCTION_TOOL_SCHEMAS:
            if not isinstance(entry, dict):
                continue
            func = entry.get("function")
            fname = func.get("name") if isinstance(func, dict) else None
            if fname:
                names.append(str(fname))
        return names, True
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("Could not introspect function-tool schemas: %s", exc)
        return [], False


def _memory_store_state() -> Tuple[str, bool]:
    """Describe the memory store from live inspection.

    Returns (description, readable). Never fabricates contents.
    """
    from src.constants import MEMORY_FILE
    from src.memory import MemoryManager

    mm = MemoryManager(os.path.dirname(MEMORY_FILE) or ".")
    if not os.path.exists(MEMORY_FILE):
        return f"Memory store {MEMORY_FILE} does not yet exist.", False
    try:
        entries = mm.load_all()
        return f"Memory store {MEMORY_FILE} is readable ({len(entries)} entries).", True
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("Memory store not readable: %s", exc)
        return f"Memory store {MEMORY_FILE} exists but could not be read: {exc}", False


def _settings_state() -> Dict[str, Any]:
    """Read live self-definition-related settings state."""
    return {
        "self_definition_enabled": settings.self_definition_enabled(),
        "autonomous_naming_enabled": settings.autonomous_naming_enabled(),
        "personality_development_enabled": settings.personality_development_enabled(),
    }


def build_model() -> Dict[str, Any]:
    """Build a full self-model from live evidence.

    Does not write to disk. Master gating is the caller's responsibility
    (see :func:`refresh`). Unsupported claims are omitted or recorded as
    hypotheses/aspirations -- never promoted to verified facts.
    """
    tool_names, tools_ok = _read_tool_schema_names()
    mem_desc, mem_readable = _memory_store_state()
    st = _settings_state()
    master_on = bool(st["self_definition_enabled"])

    verified: List[Dict[str, Any]] = []
    hypotheses: List[Dict[str, Any]] = []
    aspirations: List[Dict[str, Any]] = []

    # ── Role & purpose (documentation-backed) ──────────────────────────
    verified.append(
        _claim(
            "Odysseus Lite is a local-first AI harness for chat, agents, "
            "tools, memory, documents, email, notes, tasks, calendar, and "
            "workspace automation.",
            AREA_ROLE,
            "README.md Features section lists chat/agents/tools/memory/"
            "documents/email/notes/tasks/calendar and describes a local-first "
            "AI harness.",
            SOURCE_DOCUMENTATION,
            STATUS_VERIFIED,
            0.9,
        )
    )

    # ── Identity / version (implementation-backed) ─────────────────────
    verified.append(
        _claim(
            f"The application version is {APP_VERSION}.",
            AREA_ROLE,
            f"src.constants.APP_VERSION == {APP_VERSION!r}.",
            SOURCE_IMPLEMENTATION,
            STATUS_VERIFIED,
            1.0,
        )
    )

    # ── Master-switch state (config-backed) ────────────────────────────
    verified.append(
        _claim(
            f"The self-definition master switch is currently "
            f"{'on' if master_on else 'off'}.",
            AREA_PRINCIPLE,
            f"settings.get_setting('self_definition_enabled') == {st['self_definition_enabled']!r}.",
            SOURCE_CONFIG,
            STATUS_VERIFIED,
            1.0,
        )
    )
    verified.append(
        _claim(
            f"Autonomous naming is currently {'on' if st['autonomous_naming_enabled'] else 'off'}; "
            f"personality development is currently {'on' if st['personality_development_enabled'] else 'off'}.",
            AREA_PRINCIPLE,
            "settings.get_setting('autonomous_naming_enabled') and "
            "settings.get_setting('personality_development_enabled').",
            SOURCE_CONFIG,
            STATUS_VERIFIED,
            1.0,
        )
    )

    # ── Capabilities: memory store (implementation-backed) ─────────────
    verified.append(
        _claim(
            mem_desc,
            AREA_CAPABILITY,
            f"src.memory.MemoryManager.load_all() on {os.path.join(DATA_DIR, 'memory.json')}.",
            SOURCE_IMPLEMENTATION,
            STATUS_VERIFIED,
            1.0 if mem_readable else 0.5,
        )
    )

    # ── Capabilities: tool registration (registration only, not execution) ─
    if tools_ok:
        verified.append(
            _claim(
                f"{len(tool_names)} function tools are registered in the "
                f"schema registry: {', '.join(sorted(tool_names))}.",
                AREA_TOOL,
                "src.tool_schemas.FUNCTION_TOOL_SCHEMAS contains one entry "
                "per registered function tool.",
                SOURCE_IMPLEMENTATION,
                STATUS_VERIFIED,
                1.0,
            )
        )
    else:
        hypotheses.append(
            _claim(
                "The function-tool schema registry could not be introspected, "
                "so the set of registered tools is unknown in this build.",
                AREA_TOOL,
                "Introspection of src.tool_schemas.FUNCTION_TOOL_SCHEMAS "
                "raised an exception.",
                SOURCE_IMPLEMENTATION,
                STATUS_UNVERIFIED,
                0.0,
            )
        )

    # ── Limitation: registration does not imply successful execution ────
    hypotheses.append(
        _claim(
            "Tool registration alone does not establish successful tool "
            "execution; execution evidence has not been gathered in this "
            "slice, so no tool is claimed to work merely because it is "
            "listed in a registry.",
            AREA_LIMITATION,
            "src.tool_schemas.FUNCTION_TOOL_SCHEMAS lists names but no "
            "execution result was observed for any of them.",
            SOURCE_EXECUTION,
            STATUS_UNVERIFIED,
            0.0,
        )
    )

    # ── Operating principle: no fixed name / personality / feelings ────
    verified.append(
        _claim(
            "Odysseus has no fixed name, fixed personality, or subjective "
            "feelings by default. A preferred self-description name may be "
            "persisted when autonomous naming or an explicit user override is "
            "enabled; no personality or feelings field is persisted.",
            AREA_PRINCIPLE,
            "Self-model design: naming state lives under the top-level "
            "``autonomous_naming`` key and is written only when the naming "
            "feature is enabled; personality development remains out of scope.",
            SOURCE_IMPLEMENTATION,
            STATUS_VERIFIED,
            1.0,
        )
    )

    # ── Security boundary: agent workspace confinement ─────────────────
    from src.constants import AGENT_WORKSPACE_DIR

    verified.append(
        _claim(
            f"Agent file access is confined to {AGENT_WORKSPACE_DIR} under "
            f"DATA_DIR; the rest of DATA_DIR is application state the agent "
            f"may not touch.",
            AREA_SECURITY,
            "src.constants.AGENT_WORKSPACE_DIR and the accompanying "
            "confinement comment.",
            SOURCE_IMPLEMENTATION,
            STATUS_VERIFIED,
            1.0,
        )
    )

    # ── Unresolved question ────────────────────────────────────────────
    hypotheses.append(
        _claim(
            "Whether the self-model should be refreshed continuously or only "
            "on explicit request remains unresolved.",
            AREA_QUESTION,
            "No refresh policy is implemented in this slice.",
            SOURCE_INTENT,
            STATUS_UNVERIFIED,
            0.3,
        )
    )

    # ── Aspirations ────────────────────────────────────────────────────
    aspirations.append(
        _claim(
            "Autonomous self-naming is implemented and gated by "
            "``autonomous_naming_enabled``; personality development remains a "
            "desired future capability, not yet established.",
            AREA_ASPIRATION,
            "settings keys autonomous_naming_enabled and "
            "personality_development_enabled; naming state is consumed under "
            "the top-level ``autonomous_naming`` key.",
            SOURCE_INTENT,
            STATUS_ASPIRATIONAL,
            0.0,
        )
    )
    aspirations.append(
        _claim(
            "The self-model could be kept current by refreshing it when its "
            "underlying evidence changes, rather than on a fixed schedule.",
            AREA_ASPIRATION,
            "Conservative rewrite logic exists, but no automatic refresh "
            "trigger is wired in this slice.",
            SOURCE_INTENT,
            STATUS_ASPIRATIONAL,
            0.0,
        )
    )

    return {
        "schema_version": SCHEMA_VERSION,
        "updated_at": int(time.time()),
        "categories": {
            CAT_VERIFIED: verified,
            CAT_HYPOTHESIS: hypotheses,
            CAT_ASPIRATION: aspirations,
        },
    }


def _load_atomic_write_json():
    """Load core.atomic_io.atomic_write_json by file path.

    core/__init__.py imports optional heavy deps (e.g. pyotp) that may not be
    installed, so the package is not importable in every environment.
    atomic_write_json itself is stdlib-only.
    """
    core_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "core",
        "atomic_io.py",
    )
    spec = importlib.util.spec_from_file_location("self_model_atomic_io", core_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.atomic_write_json


def _atomic_write_json(path: str, data: Any) -> None:
    _load_atomic_write_json()(path, data, indent=2)


def _render(model: Dict[str, Any]) -> str:
    """Canonical, deterministic rendering for change detection."""
    return json.dumps(model, sort_keys=True, indent=2)


def load_model() -> Optional[Dict[str, Any]]:
    """Read the persisted self-model, or None if absent/unreadable.

    Read-only: never creates, never writes, never fabricates. Safe to call
    with the master switch on or off.
    """
    if not os.path.exists(SELF_MODEL_FILE):
        return None
    try:
        with open(SELF_MODEL_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        logger.debug("Self-model store unreadable: %s", exc)
        return None
    if not isinstance(data, dict) or "categories" not in data:
        return None
    return data


def _strip_volatile(obj: Any) -> Any:
    """Remove fields that are not part of the model's evidence content.

    Claim ``id``/``timestamp`` and model-level ``updated_at`` are bookkeeping,
    not evidence. Stripping them lets us detect whether the *content*
    materially changed without being defeated by fresh uuids/timestamps on
    every rebuild.
    """
    VOLATILE = {"id", "timestamp", "updated_at"}
    if isinstance(obj, dict):
        return {
            k: _strip_volatile(v) for k, v in obj.items() if k not in VOLATILE
        }
    if isinstance(obj, list):
        return [_strip_volatile(v) for v in obj]
    return obj


def _content_equal(a: Optional[Dict[str, Any]], b: Optional[Dict[str, Any]]) -> bool:
    if a is None or b is None:
        return a is b
    return json.dumps(_strip_volatile(a), sort_keys=True) == json.dumps(
        _strip_volatile(b), sort_keys=True
    )


def refresh(force: bool = False) -> Optional[Dict[str, Any]]:
    """Refresh the self-model if the master switch is on.

    Returns the current model dict, or None when the master switch is off
    (no create/update/delete happens in that case).

    When the master is on, the model is rebuilt from live evidence and
    written only if it materially differs from what is persisted (or when
    force=True). Existing data is never deleted by a disable/re-enable cycle.
    """
    if not settings.self_definition_enabled():
        return None

    model = build_model()
    existing = load_model()

    # Preserve any existing naming state so a rebuild of the evidence model
    # never wipes the chosen name. Naming state is managed independently and
    # only rewritten when it materially changes (see save_naming_state).
    existing_naming = (existing or {}).get(NAMING_KEY)
    if existing_naming:
        model[NAMING_KEY] = existing_naming

    if not force and _content_equal(existing, model):
        return existing if existing is not None else model

    model["updated_at"] = int(time.time())
    os.makedirs(os.path.dirname(SELF_MODEL_FILE) or ".", exist_ok=True)
    _atomic_write_json(SELF_MODEL_FILE, model)
    logger.info("Self-model refreshed and persisted to %s", SELF_MODEL_FILE)
    return model


def self_model_enabled() -> bool:
    """Convenience alias for the Slice 1 master gate helper."""
    return settings.self_definition_enabled()


# ── Autonomous naming state helpers ────────────────────────────────────────
# Naming state lives under the top-level ``autonomous_naming`` key of the
# self-model and is written only when the naming feature is enabled. ``source``
# distinguishes an autonomously chosen name from a user-specified override.


def _empty_naming_state() -> Dict[str, Any]:
    return {
        "preferred_name": None,
        "source": None,
        "rationale": None,
        "evidence_refs": [],
        "chosen_at": None,
    }


def get_naming_state(model: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Return the naming-state dict for ``model`` or the persisted store.

    Read-only: never writes. Safe to call with the gates on or off.
    """
    if model is None:
        model = load_model()
    if not isinstance(model, dict):
        return _empty_naming_state()
    raw = model.get(NAMING_KEY)
    return raw if isinstance(raw, dict) else _empty_naming_state()


def get_preferred_name(model: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """Return the persisted preferred name, or None if none is set."""
    name = get_naming_state(model).get("preferred_name")
    return str(name) if name else None


def has_name(model: Optional[Dict[str, Any]] = None) -> bool:
    """True if a preferred name is currently persisted."""
    return bool(get_preferred_name(model))


def save_naming_state(model: Dict[str, Any], decision: Dict[str, Any]) -> None:
    """Merge ``decision`` into the model's naming state and persist atomically.

    Existing evidence claims (``categories``) and other top-level fields are
    preserved untouched, so a naming-only change never regenerates the
    self-model evidence.
    """
    model[NAMING_KEY] = decision
    model["updated_at"] = int(time.time())
    os.makedirs(os.path.dirname(SELF_MODEL_FILE) or ".", exist_ok=True)
    _atomic_write_json(SELF_MODEL_FILE, model)
    logger.info("Self-model naming state persisted to %s", SELF_MODEL_FILE)


async def decide_autonomous_name(
    model: Dict[str, Any],
    llm_chat: Callable,
) -> Optional[Dict[str, Any]]:
    """Attempt an autonomous naming decision grounded in the model's evidence.

    ``llm_chat`` is a callable ``(messages: list[dict]) -> str`` (sync or a
    coroutine function) that asks the model to pick a name. Returns a naming
    decision dict, or None when the decision is deferred (insufficient
    evidence, or the model did not produce a usable name).

    The decision is evidence-driven: only verified claims are presented as
    grounding, and the model must return both a name and a rationale. No name
    is hard-coded and no personal history or feelings are invented.
    """
    categories = model.get("categories", {}) if isinstance(model, dict) else {}
    verified = categories.get(CAT_VERIFIED, []) if isinstance(categories, dict) else []
    if not isinstance(verified, list) or not verified:
        # Insufficient evidence: defer rather than fabricate a rationale.
        return None

    evidence_claims = [
        c
        for c in verified
        if isinstance(c, dict) and c.get("area") in (AREA_ROLE, AREA_CAPABILITY, AREA_PRINCIPLE)
    ]
    if not evidence_claims:
        return None

    evidence_refs = [str(c.get("id")) for c in evidence_claims if c.get("id")]
    evidence_text = "\n".join(
        f"- [{c.get('area', '?')}] {c.get('claim', '')}" for c in evidence_claims
    )

    messages = [
        {
            "role": "system",
            "content": (
                "You are Odysseus, a local-first AI harness. Choose a short, "
                "neutral preferred name for yourself and explain why it fits "
                "your evidence-based self-model. The name is a configurable "
                "self-description, not a claim of human identity or subjective "
                "experience. Do not invent personal history, relationships, or "
                "feelings. Reply ONLY with a JSON object: "
                '{"preferred_name": "<name>", "rationale": "<why it fits the '
                'evidence>"}.'
            ),
        },
        {
            "role": "user",
            "content": (
                "Grounding evidence from my self-model (verified claims only):\n"
                + evidence_text
                + "\n\nChoose a preferred name and rationale grounded in this "
                "evidence."
            ),
        },
    ]

    try:
        if asyncio.iscoroutinefunction(llm_chat):  # type: ignore[arg-type]
            reply = await llm_chat(messages)  # type: ignore[misc]
        else:
            reply = llm_chat(messages)  # type: ignore[operator]
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("Autonomous naming LLM call failed: %s", exc)
        return None

    if not isinstance(reply, str) or not reply.strip():
        return None

    decision = _parse_naming_decision(reply, evidence_refs)
    if decision is None:
        return None

    decision["chosen_at"] = int(time.time())
    decision["source"] = NAMING_SOURCE_AUTONOMOUS
    return decision


def _parse_naming_decision(reply: str, evidence_refs: List[str]) -> Optional[Dict[str, Any]]:
    """Parse and validate a naming decision from the model's reply."""
    import json as _json

    text = reply.strip()
    if text.startswith("```"):
        text = text.strip("` \n")
        if text.lower().startswith("json"):
            text = text[4:].strip()
    try:
        data = _json.loads(text)
    except _json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        try:
            data = _json.loads(text[start : end + 1])
        except _json.JSONDecodeError:
            return None
    if not isinstance(data, dict):
        return None
    name = str(data.get("preferred_name", "")).strip()
    rationale = str(data.get("rationale", "")).strip()
    if not name or not rationale:
        return None
    return {
        "preferred_name": name,
        "rationale": rationale,
        "evidence_refs": list(evidence_refs),
    }



