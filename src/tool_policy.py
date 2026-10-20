"""Per-turn tool policy composition for agent execution."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Iterable, Mapping, Optional, Set, Tuple


GUIDE_ONLY_DIRECTIVE = (
    "## GUIDE-ONLY MODE - TOOL POLICY\n"
    "The latest user turn explicitly forbids tool use. Do not call tools, do not "
    "run shell commands, and do not inspect local files or the environment. "
    "Respond in normal text by guiding the user or asking them to paste the "
    "output they will produce locally."
)

ODYSSEUS_DEFAULT_INTERACTION_RULES = (
    "## Conversation and personal-state defaults\n"
    "Treat casual conversation and jokes as valid goals. When the user wants to "
    "chat or asks for a distraction, stay with that intent; do not steer toward "
    "work, projects, or job hunting unless they ask. If they ask you to choose a "
    "distraction, pick one light, non-work topic yourself instead of offering a "
    "menu or turning it into a plan. A mention of a work problem or future job "
    "search is not by itself a request to search or make a plan.\n"
    "When the user shares an emotional state, acknowledge it naturally and let "
    "them choose whether to talk or be distracted. Do not infer a request for "
    "web search, advice, or action. Do not default to crisis-resource language "
    "for ordinary low mood; follow safety guidance when the user indicates "
    "immediate danger. Respect stated off-limits topics, and do not interpret a "
    "hobby as therapy or emotional processing."
)

CONVERSATION_ONLY_DIRECTIVE = (
    "## CASUAL CONVERSATION - TOOL POLICY\n"
    "The user wants casual conversation, a distraction, or has shared a personal "
    "update. Respond to the topic they actually raised. Do not infer a task from "
    "stored profile details, redirect the conversation to work, mention their "
    "job search or project, or remind them to return to work unless they ask. "
    "When they ask you to choose a distraction, choose one light, non-work topic "
    "yourself; do not offer work-related options or turn the chat into a plan, "
    "lesson, or productivity exercise. Keep hobbies conversational and do not "
    "interpret them as therapy or emotional processing. Respect any explicit "
    "off-limits topic without revisiting or hinting at it. For an ordinary low-mood "
    "disclosure without signs of immediate danger, acknowledge it briefly and "
    "offer the choice to talk or be distracted; do not default to crisis-resource "
    "boilerplate. Follow safety guidance if the user indicates imminent danger. "
    "Be candid and avoid unsupported reassurance or flattery. Do not narrate tool "
    "availability or repeat a previous tool action. Do not call tools unless the "
    "user explicitly asks for a specific tool-backed action in this turn."
)

WEB_TOOL_NAMES = frozenset({"web_search", "web_fetch"})


_FACTUAL_LOOKUP_RE = re.compile(
    r"\b(?:what|who|when|where|why|how|which|whose|whom|is|are|was|were|"
    r"does|do|did|can|could|would|should|has|have|had|will|define|explain|"
    r"describe|compare|identify|list|tell\s+me\s+about|what'?s)\b",
    re.IGNORECASE,
)
_PERSONAL_CONTEXT_QUERY_RE = re.compile(
    r"\bhow\s+should\s+you\s+format\s+(?:your\s+)?answers\s+for\s+me\b|"
    r"\bwhat\s+do\s+you\s+know\s+about\s+me\b|"
    r"\bwhat(?:'s|\s+is)\s+my\s+(?:name|preferences?|likes|dislikes|profile)\b",
    re.IGNORECASE,
)
_SMALL_MODEL_SIZE_RE = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)\s*b\b", re.IGNORECASE)


def is_factual_lookup_request(message: object) -> bool:
    """Identify questions and direct requests for factual explanations."""

    if not isinstance(message, str):
        return False
    text = message.strip()
    if not text or len(text) > 4000:
        return False
    # Questions about what the assistant remembers should be answered from
    # user-owned context, not sent to the web-grounding heuristic.
    if _PERSONAL_CONTEXT_QUERY_RE.search(text):
        return False
    if re.search(
        r"\b(?:write|draft|compose|brainstorm|imagine|roleplay|translate|"
        r"summarize\s+this|fix\s+this\s+code|debug\s+this)\b",
        text,
        re.IGNORECASE,
    ):
        return False
    return "?" in text or bool(_FACTUAL_LOOKUP_RE.search(text))


def is_small_model_identifier(model: object, max_parameters_b: float = 8.0) -> bool:
    """Recognize a small model when its identifier states a parameter count."""

    if not isinstance(model, str):
        return False
    match = _SMALL_MODEL_SIZE_RE.search(model)
    if not match:
        return False
    try:
        return float(match.group(1)) <= max_parameters_b
    except ValueError:
        return False


def tool_toggle_enabled(value: object) -> bool:
    """Return true only for explicit true-like tool toggle values."""

    return str(value).lower() == "true"


def tool_toggle_explicitly_denied(value: object) -> bool:
    """Return true when a caller explicitly supplied a non-true toggle value."""

    return value is not None and not tool_toggle_enabled(value)


def is_web_search_explicitly_denied(allow_web_search: object) -> bool:
    """Whether the web-search agent toggle was explicitly set to false."""

    return tool_toggle_explicitly_denied(allow_web_search)


def web_search_enabled_for_turn(allow_web_search: object, use_web: object = None) -> bool:
    """Return true only when this request explicitly enables web search.

    Agent mode sends ``allow_web_search``; chat-mode pre-search sends
    ``use_web``. If both are present, an explicit ``allow_web_search=false``
    wins so a stale or conflicting intent path cannot re-enable web tools.
    """

    if is_web_search_explicitly_denied(allow_web_search):
        return False
    return tool_toggle_enabled(allow_web_search) or tool_toggle_enabled(use_web)


_COMMON_TOOL_NAMES = {
    "api_call",
    "app_api",
    "archive_email",
    "ask_teacher",
    "ask_user",
    "bash",
    "bulk_email",
    "builtin_browser",
    "cancel_download",
    "chat_with_model",
    "create_document",
    "create_session",
    "delete_email",
    "download_model",
    "edit_document",
    "edit_file",
    "edit_image",
    "generate_image",
    "glob",
    "grep",
    "list_cached_models",
    "list_cookbook_servers",
    "list_downloads",
    "list_emails",
    "list_models",
    "list_serve_presets",
    "list_served_models",
    "list_sessions",
    "ls",
    "manage_calendar",
    "manage_contact",
    "manage_documents",
    "manage_endpoints",
    "manage_mcp",
    "manage_memory",
    "manage_notes",
    "manage_research",
    "manage_session",
    "manage_settings",
    "manage_skills",
    "manage_tasks",
    "manage_tokens",
    "manage_webhooks",
    "mark_email_read",
    "pipeline",
    "python",
    "read_email",
    "read_file",
    "reply_to_email",
    "resolve_contact",
    "search_chats",
    "search_hf_models",
    "send_email",
    "send_to_session",
    "serve_model",
    "serve_preset",
    "stop_served_model",
    "suggest_document",
    "trigger_research",
    "ui_control",
    "update_document",
    "update_plan",
    "vault_get",
    "vault_search",
    "vault_unlock",
    "web_fetch",
    "web_search",
    "write_file",
}


_GUIDE_ONLY_PATTERNS: Tuple[Tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pattern, re.IGNORECASE), reason)
    for pattern, reason in (
        (r"\bguide[-\s]?only mode\b", "guide-only mode requested"),
        (r"\bno[-\s]?tools? mode\b", "no-tools mode requested"),
        (r"\bdo not use (?:any )?tools?\b", "user forbade tool use"),
        (r"\bdon'?t use (?:any )?tools?\b", "user forbade tool use"),
        (r"\bnot allowed to use (?:any )?tools?\b", "user forbade tool use"),
        (r"\bnot allowed to:?.{0,120}\buse (?:any )?tools?\b", "user forbade tool use"),
        (r"\bask (?:me )?(?:for confirmation )?before using tools?\b", "user requested confirmation before tools"),
    )
)

_CONVERSATION_ONLY_PATTERNS: Tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bhow\s+are\s+you(?:\s+doing)?(?:\s+(?:today|tonight))?[\s?.!]*$",
        r"\bhow'?s\s+your\s+day\b",
        r"\b(?:just\s+)?chatting\b",
        r"\b(?:no|nah|nope)\s*[,—-]?\s*just\s+(?:chat|talk)\b",
        r"\b(?:i'?m|i\s+am)\s+just\s+(?:chilling|relaxing)\b",
        r"\bjust\s+(?:chilling|relaxing)\b",
        r"\b(?:i\s+)?(?:just\s+)?(?:want|wanna|would\s+like)\s+to\s+chat\b",
        r"\b(?:let'?s|can\s+we|could\s+we)\s+(?:just\s+)?chat\b",
        r"\bget\s+my\s+mind\s+off\b",
        r"\b(?:something|anything)\s+else\b.{0,100}\b(?:just\s+)?(?:chat|talk|relax|distract)\b",
        r"\b(?:shoot(?:ing)?\s+the\s+breeze|just\s+banter(?:ing)?|just\s+talk(?:ing)?)\b",
        r"\b(?:tell|give)\s+me\s+(?:(?:a\s+)?good\s+|a\s+|another\s+|one\s+)?joke\b",
        r"\b(?:don'?t|do\s+not)\s+(?:remind|steer|redirect)\s+me\s+(?:back\s+)?to\s+(?:actual\s+)?work\b",
        r"\bwhen\s+i\s+(?:chat|joke|just\s+talk)\b.{0,160}\b(?:away\s+from\s+work|distraction|need\s+a\s+break)\b",
    )
)

_PERSONAL_STATE_ONLY_PATTERNS: Tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\b(?:i'?m|i\s+am|i\s+feel|i'?ve\s+been\s+feeling)\s+(?:feeling\s+)?"
        r"(?:a\s+bit\s+|a\s+little\s+|pretty\s+|really\s+|quite\s+|so\s+)?"
        r"(?:ill|sick|unwell|tired|exhausted|stressed|overwhelmed|sad|down|"
        r"depressed|anxious|worried|lonely|frustrated|happy|excited|rough)\b",
        r"\b(?:today|tonight)\b.{0,40}\b(?:feel|feeling)\s+"
        r"(?:ill|sick|unwell|tired|stressed|sad|down|anxious|rough)\b",
    )
)

_EXPLICIT_ACTION_REQUEST_RE = re.compile(
    r"\b(?:can|could|would|will)\s+you\s+(?:please\s+)?"
    r"(?:search|look\s+up|find|book|send|create|open|run|execute|call|email|"
    r"generate|edit|save|delete|update|compare|research|help\s+me\s+find)\b"
    r"|\bplease\s+(?:search|look\s+up|find|book|send|create|open|run|execute|"
    r"call|email|generate|edit|save|delete|update|compare|research)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ToolPolicy:
    """Effective tool behavior for one agent turn."""

    disabled_tools: frozenset[str] = frozenset()
    hidden_tools: frozenset[str] = frozenset()
    reasons: Mapping[str, str] = field(default_factory=dict)
    mode: str = "normal"
    block_all_tool_calls: bool = False
    disable_mcp: bool = False

    def all_disabled_names(self) -> Set[str]:
        return set(self.disabled_tools) | set(self.hidden_tools)

    def blocks(self, tool_name: Optional[str]) -> bool:
        if not tool_name:
            return False
        return self.block_all_tool_calls or tool_name in self.disabled_tools or tool_name in self.hidden_tools

    def reason_for(self, tool_name: Optional[str]) -> str:
        if tool_name and tool_name in self.reasons:
            return self.reasons[tool_name]
        if self.block_all_tool_calls and self.mode == "guide_only":
            return "Tool use is disabled for this guide-only turn."
        if self.block_all_tool_calls and self.mode == "conversation_only":
            return "Tool use is disabled because the user asked for casual conversation."
        return "Tool use is disabled for this turn."


def detect_guide_only_turn(message: object) -> Optional[str]:
    """Return a reason when the latest user turn strongly requests no tools."""

    if not isinstance(message, str) or not message.strip():
        return None
    text = re.sub(r"\s+", " ", message.strip())
    for pattern, reason in _GUIDE_ONLY_PATTERNS:
        if pattern.search(text):
            return reason
    return None


def detect_conversation_only_turn(message: object) -> Optional[str]:
    """Return a reason when the user is chatting or sharing a personal update."""

    if not isinstance(message, str) or not message.strip():
        return None
    text = re.sub(r"\s+", " ", message.strip())
    if _EXPLICIT_ACTION_REQUEST_RE.search(text):
        return None
    if any(pattern.search(text) for pattern in _CONVERSATION_ONLY_PATTERNS):
        return "casual conversation or topic change requested"
    if any(pattern.search(text) for pattern in _PERSONAL_STATE_ONLY_PATTERNS):
        return "personal state shared without a request for action"
    return None


def known_tool_names() -> Set[str]:
    """Best-effort set of native tool names for prompt hiding and denylisting."""

    names = set(_COMMON_TOOL_NAMES)
    try:
        from src.tool_schemas import FUNCTION_TOOL_SCHEMAS

        for schema in FUNCTION_TOOL_SCHEMAS:
            name = (schema.get("function") or {}).get("name") or schema.get("name")
            if name:
                names.add(name)
    except Exception:
        pass
    try:
        from src.agent_loop import TOOL_SECTIONS

        names.update(TOOL_SECTIONS.keys())
    except Exception:
        pass
    try:
        from src.tool_security import PLAN_MODE_READONLY_TOOLS, _PLAN_MODE_KNOWN_MUTATORS

        names.update(PLAN_MODE_READONLY_TOOLS)
        names.update(_PLAN_MODE_KNOWN_MUTATORS)
    except Exception:
        pass
    return names


def build_effective_tool_policy(
    *,
    disabled_tools: Optional[Iterable[str]] = None,
    last_user_message: object = "",
) -> ToolPolicy:
    """Compose the effective policy for one agent turn.

    Existing callers still provide the already-composed disabled-tool denylist.
    This function adds higher-level turn policy on top so enforcement is not
    delegated to prompt compliance.
    """

    disabled = {str(t) for t in (disabled_tools or []) if t}
    hidden: Set[str] = set()
    reasons = {tool: "Tool is disabled for this request." for tool in disabled}

    guide_reason = detect_guide_only_turn(last_user_message)
    if guide_reason:
        all_tools = known_tool_names()
        disabled.update(all_tools)
        hidden.update(all_tools)
        reasons.update({tool: f"{guide_reason}." for tool in all_tools})
        return ToolPolicy(
            disabled_tools=frozenset(disabled),
            hidden_tools=frozenset(hidden),
            reasons=MappingProxyType(dict(reasons)),
            mode="guide_only",
            block_all_tool_calls=True,
            disable_mcp=True,
        )

    conversation_reason = detect_conversation_only_turn(last_user_message)
    if conversation_reason:
        all_tools = known_tool_names()
        disabled.update(all_tools)
        hidden.update(all_tools)
        reasons.update({tool: f"{conversation_reason}." for tool in all_tools})
        return ToolPolicy(
            disabled_tools=frozenset(disabled),
            hidden_tools=frozenset(hidden),
            reasons=MappingProxyType(dict(reasons)),
            mode="conversation_only",
            block_all_tool_calls=True,
            disable_mcp=True,
        )

    return ToolPolicy(
        disabled_tools=frozenset(disabled),
        hidden_tools=frozenset(hidden),
        reasons=MappingProxyType(dict(reasons)),
    )
