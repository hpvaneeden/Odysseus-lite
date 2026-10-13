"""Conservative compaction of redundant social turns from model context."""

import re


_SOCIAL_QUESTIONS = frozenset({
    "what's up", "how are you", "how's it going", "how are things",
})
_EXPLICIT_REQUEST_RE = re.compile(
    r"\b(?:can|could|would|will) you\b|"
    r"\b(?:please\s+)?(?:find|search|look up|tell me|show me|explain|recommend|"
    r"suggest|calculate|compare|check|remember|save|forget|create|write|run)\b|"
    r"\b(?:what|who|where|when|why|how|which)\s+(?:is|are|was|were|do|does|"
    r"did|can|could|would|will|should|means?|happens?)\b|"
    r"\b(?:latest|current)\b.{0,40}\b(?:rate|weather|news|price|version|status|results?)\b|"
    r"\bnear me\b",
    re.IGNORECASE,
)

_USER_PLEASANTRIES = frozenset({
    "hi", "hello", "hey", "hey there", "yo", "sup",
    "thanks", "thank you", "thx", "ty",
    "bye", "goodbye", "good night", "good morning", "good evening",
})
_ASSISTANT_PLEASANTRIES = frozenset({
    "hi", "hello", "hey", "hey there", "hello there",
    "hi! how can i help", "hello! how can i help", "hey! how can i help",
    "hi! how can i help you today", "hello! how can i help you today",
    "hey! how can i help you today", "how can i help", "how can i help you today",
    "what can i do for you", "you are welcome", "you're welcome", "no problem",
    "anytime", "bye", "goodbye", "take care", "good night", "good morning",
})


def _normalized_content(message: dict) -> str:
    content = message.get("content")
    if not isinstance(content, str):
        return ""
    return " ".join(content.casefold().split()).strip(" .!?\t\r\n")


def is_explicit_request(text: str) -> bool:
    """Protect concise questions and actions from low-signal social routing."""
    normalized = " ".join(str(text or "").casefold().split())
    if normalized.strip(" .!?\t\r\n") in _SOCIAL_QUESTIONS:
        return False
    return "?" in normalized or bool(_EXPLICIT_REQUEST_RE.search(normalized))


def compact_social_history(messages: list[dict]) -> list[dict]:
    """Drop only completed, exact pleasantry exchanges from a prompt copy.

    The latest user turn, emotional or substantive text, tool turns, and the
    stored session history are left untouched. Ambiguous content is preserved.
    """
    latest_user = next(
        (i for i in range(len(messages) - 1, -1, -1) if messages[i].get("role") == "user"),
        None,
    )
    if latest_user is None:
        return list(messages)

    compacted = []
    index = 0
    while index < len(messages):
        if index + 1 < latest_user:
            user_message = messages[index]
            assistant_message = messages[index + 1]
            is_plain_pair = (
                user_message.get("role") == "user"
                and assistant_message.get("role") == "assistant"
                and not assistant_message.get("tool_calls")
                and not assistant_message.get("function_call")
            )
            if is_plain_pair and (
                _normalized_content(user_message) in _USER_PLEASANTRIES
                and _normalized_content(assistant_message) in _ASSISTANT_PLEASANTRIES
            ):
                index += 2
                continue

        compacted.append(messages[index])
        index += 1
    return compacted