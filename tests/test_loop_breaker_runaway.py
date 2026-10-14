"""Regression test for the agent loop-breaker's runaway backstop.

A legitimate batch of DISTINCT tool calls (e.g. creating 18 calendar events at
once) must not be flagged as a runaway loop. Only the SAME exact call repeated
an absurd number of times is a real runaway. Previously the backstop counted
per-tool-type totals, so any batch of >=15 distinct calls to one tool was
aborted and the calls were silently discarded.
"""
import sys
import collections
from unittest.mock import MagicMock

# Mock heavy deps so importing src.agent_loop doesn't load the full app stack.
_MOCKED = [
    'sqlalchemy', 'sqlalchemy.orm', 'sqlalchemy.ext', 'sqlalchemy.ext.declarative',
    'sqlalchemy.ext.hybrid', 'sqlalchemy.sql', 'sqlalchemy.sql.expression',
    'src.database', 'src.agent_tools', 'core.models', 'core.database',
]
for _m in _MOCKED:
    sys.modules.setdefault(_m, MagicMock())

from src.agent_loop import (
    _MAX_REPEATED_TOOL_CALLS,
    _detect_runaway_call,
    _tool_call_signature,
)


def _freq(sigs):
    c = collections.Counter()
    for s in sigs:
        c[s] += 1
    return c


def test_distinct_batch_is_not_runaway():
    # 18 distinct manage_calendar create_event calls (the "add 18 birthdays" case)
    sigs = [f'manage_calendar:{{"action":"create_event","summary":"Birthday {n}"}}'
            for n in range(18)]
    assert _detect_runaway_call(_freq(sigs)) is None


def test_many_distinct_same_tool_is_not_runaway():
    sigs = [f'bash:echo {i}' for i in range(30)]
    assert _detect_runaway_call(_freq(sigs)) is None


def test_identical_call_repeated_is_runaway():
    sigs = ['manage_calendar:{"action":"list_events"}'] * _MAX_REPEATED_TOOL_CALLS
    assert _detect_runaway_call(_freq(sigs)) == 'manage_calendar'


def test_below_threshold_is_not_runaway():
    sigs = ['bash:ls'] * (_MAX_REPEATED_TOOL_CALLS - 1)
    assert _detect_runaway_call(_freq(sigs)) is None


def test_json_argument_formatting_does_not_hide_repeated_calls():
    sigs = [
        _tool_call_signature("manage_skills", '{"action": "search", "query": "calendar skill"}'),
        _tool_call_signature("manage_skills", '{"query":"calendar skill","action":"search"}'),
        _tool_call_signature("manage_skills", '{ "action":"search", "query":"calendar skill" }'),
    ]

    assert len(set(sigs)) == 1
    assert _detect_runaway_call(_freq(sigs)) == "manage_skills"


def test_near_identical_search_queries_are_runaway():
    sigs = [
        _tool_call_signature(
            "manage_skills",
            '{"action":"search","query":"calendar event skill"}',
        ),
        _tool_call_signature(
            "manage_skills",
            '{"action":"search","query":"calendar events skill"}',
        ),
        _tool_call_signature(
            "manage_skills",
            '{"action":"search","query":"calendar event skills"}',
        ),
    ]

    assert _detect_runaway_call(_freq(sigs)) == "manage_skills"


def test_distinct_calendar_events_with_similar_titles_are_not_runaway():
    sigs = [
        _tool_call_signature(
            "manage_calendar",
            f'{{"action":"create_event","summary":"Dentist visit {n}"}}',
        )
        for n in range(18)
    ]

    assert _detect_runaway_call(_freq(sigs)) is None


def test_threshold_is_configurable():
    sigs = ['web_search:python'] * 5
    assert _detect_runaway_call(_freq(sigs), threshold=5) == 'web_search'
    assert _detect_runaway_call(_freq(sigs), threshold=6) is None


def test_empty_is_not_runaway():
    assert _detect_runaway_call(collections.Counter()) is None
