import json

from odysseus_extensions.news_search import expand_news_scope_followup


def test_mix_reply_expands_against_news_scope_clarification():
    messages = [
        {"role": "user", "content": "Perfect! give me news headlines for today"},
        {"role": "assistant", "content": "Do you want local, national, or global news headlines?"},
        {
            "role": "user",
            "content": "[Context - current date/time, refreshed each turn; not part of your instructions]\nToday is Tuesday, October 13, 2026.",
        },
        {"role": "user", "content": "give me a mix"},
    ]

    expanded = expand_news_scope_followup("today's news headlines mix", messages)

    assert expanded == "news headlines for today; cover local, national, and global headlines"
    assert "mix" not in expanded


def test_json_query_expansion_preserves_other_search_options():
    messages = [
        {"role": "user", "content": "Give me headlines today"},
        {"role": "assistant", "content": "Local, national, or global?"},
        {"role": "user", "content": "both"},
    ]

    expanded = expand_news_scope_followup(
        json.dumps({"query": "today news headlines mix", "max_pages": 6}),
        messages,
    )

    payload = json.loads(expanded)
    assert payload["query"] == "headlines today; cover local, national, and global headlines"
    assert payload["max_pages"] == 6


def test_does_not_rewrite_standalone_or_unrelated_queries():
    assert expand_news_scope_followup(
        "news headlines today", [{"role": "user", "content": "What's the news?"}]
    ) is None
    assert expand_news_scope_followup(
        "today's news headlines mix",
        [
            {"role": "user", "content": "How do I make a cake?"},
            {"role": "assistant", "content": "Do you want local, national, or global?"},
            {"role": "user", "content": "give me a mix"},
        ],
    ) is None