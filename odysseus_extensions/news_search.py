"""News-digest guidance kept separate from Odysseus core prompts."""

import json
import re


_MIX_REPLY_RE = re.compile(
	r"^(?:please\s+)?(?:(?:give|show|provide)\s+me\s+)?"
	r"(?:a\s+)?(?:mix|both|all(?:\s+of\s+them|\s+three)?|all\s+of\s+those)"
	r"(?:\s+please)?[.!?]*$",
	re.IGNORECASE,
)
_NEWS_REQUEST_RE = re.compile(r"\b(?:news|headlines?)\b", re.IGNORECASE)
_NEWS_SCOPE_QUESTION_RE = re.compile(
	r"\b(?:local|national)\b.{0,100}\b(?:global|international|world)\b|"
	r"\b(?:global|international|world)\b.{0,100}\b(?:local|national)\b",
	re.IGNORECASE,
)

NEWS_DIGEST_RULES = """\
## News digest rules
- For a news digest, use the requested geography. If it is omitted, use a clear location preference from saved context; if none exists, ask whether the user wants local, national, or global headlines.
- When the user answers a news-scope clarification with "mix", "both", or "all", resolve that answer against the preceding request and clarification. Search explicit scopes; never send the fragment "mix" as a topical search term.
- Do not equate search-result order with "top" news. Seek current reporting from reputable sources and use a focused follow-up search when results are dominated by one niche or region.
- Treat snippets as discovery only. Fetch each selected report and verify its headline, topic, and publication date against the page.
- Exclude school-assembly roundups, astrology, forecasts, betting tips, horse-racing previews, and promotional/SEO pages unless explicitly requested.
- Give a short, balanced list of distinct headlines with source names, non-empty Markdown links, and publication dates when available. Never invent a date or describe an unverified result as a top headline.
- If the fetched pages do not support a useful digest, say so instead of padding the list with unrelated results."""


def _message_text(message: dict) -> str:
	if message.get("_agent_injected"):
		return ""
	content = message.get("content")
	if isinstance(content, list):
		content = " ".join(
			part.get("text", "") for part in content if isinstance(part, dict)
		)
	if not isinstance(content, str):
		return ""
	text = " ".join(content.split())
	if text.startswith("[Context ") and "current date/time" in text[:100].lower():
		return ""
	return text


def expand_news_scope_followup(content: str, messages: list[dict]) -> str | None:
	"""Expand an unambiguous news-scope reply before it becomes a search query."""
	raw_content = str(content or "").strip()
	payload = None
	query = raw_content
	if raw_content.startswith("{"):
		try:
			parsed = json.loads(raw_content)
		except json.JSONDecodeError:
			return None
		if not isinstance(parsed, dict) or not isinstance(parsed.get("query"), str):
			return None
		payload = parsed
		query = parsed["query"].strip()

	if not _NEWS_REQUEST_RE.search(query):
		return None

	turns = []
	for message in messages:
		if not isinstance(message, dict):
			continue
		role = message.get("role")
		if role not in {"user", "assistant"}:
			continue
		text = _message_text(message)
		if text:
			turns.append((role, text))

	if not turns or turns[-1][0] != "user":
		return None
	latest_user = turns[-1][1]
	if not _MIX_REPLY_RE.fullmatch(latest_user):
		return None

	clarification_index = next(
		(
			index for index in range(len(turns) - 2, -1, -1)
			if turns[index][0] == "assistant"
		),
		None,
	)
	if clarification_index is None or not _NEWS_SCOPE_QUESTION_RE.search(
		turns[clarification_index][1]
	):
		return None

	preceding_user = next(
		(
			turns[index][1]
			for index in range(clarification_index - 1, -1, -1)
			if turns[index][0] == "user"
		),
		"",
	)
	if not _NEWS_REQUEST_RE.search(preceding_user):
		return None

	task = re.sub(
		r"^\s*(?:(?:perfect|okay|ok|sure)[,!:.]?\s*)?"
		r"(?:(?:please\s+)?(?:give|show|provide|find|list)\s+me\s+)?",
		"",
		preceding_user,
		flags=re.IGNORECASE,
	).strip(" .!?\t\r\n")
	if not task:
		task = query
	expanded_query = f"{task}; cover local, national, and global headlines"
	if payload is None:
		return expanded_query
	payload["query"] = expanded_query
	return json.dumps(payload, ensure_ascii=False)