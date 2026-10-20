import asyncio
import json
import sys
from types import SimpleNamespace

import src.agent_loop as al
from src.agent_tools import ToolBlock
from src.tool_execution import NO_TOOL_SECURITY_CONTEXT, execute_tool_block
from src.tool_policy import (
    WEB_TOOL_NAMES,
    build_effective_tool_policy,
    detect_conversation_only_turn,
    detect_guide_only_turn,
    is_factual_lookup_request,
    web_search_enabled_for_turn,
)


def _collect(gen):
    async def _run():
        return [c async for c in gen]

    return asyncio.run(_run())


def _events(chunks):
    out = []
    for chunk in chunks:
        if chunk.startswith("data: ") and not chunk.startswith("data: [DONE]"):
            try:
                out.append(json.loads(chunk[6:]))
            except Exception:
                pass
    return out


def _delta_chunk(text):
    return "data: " + json.dumps({"delta": text}) + "\n\n"


def _patch_loop_basics(monkeypatch):
    monkeypatch.setattr(al, "get_setting", lambda key, default=None: default, raising=False)
    monkeypatch.setattr(al, "get_mcp_manager", lambda: None, raising=False)
    monkeypatch.setattr(al, "estimate_tokens", lambda *a, **k: 10, raising=False)


def test_detects_strong_guide_only_turns():
    assert detect_guide_only_turn("GUIDE-ONLY MODE. DO NOT USE TOOLS.")
    assert detect_guide_only_turn("NO-TOOLS MODE.")
    assert detect_guide_only_turn("Ask me before using tools.")
    assert detect_guide_only_turn("You are not allowed to:\n- use tools\n- execute commands")


def test_does_not_treat_ordinary_guidance_as_no_tools():
    assert detect_guide_only_turn("Can you guide me through fixing this bug?") is None
    assert detect_guide_only_turn("I have no tools installed in this project.") is None
    assert detect_guide_only_turn("Write the script in the repo; I'll run it locally.") is None
    assert detect_guide_only_turn("Do not run commands that write files; inspect the repo first.") is None
    assert detect_guide_only_turn("Don't execute shell commands unless I approve them.") is None


def test_guide_only_policy_blocks_and_hides_tools():
    policy = build_effective_tool_policy(
        disabled_tools={"web_search"},
        last_user_message="GUIDE-ONLY MODE. DO NOT USE TOOLS.",
    )
    assert policy.mode == "guide_only"
    assert policy.disable_mcp is True
    assert policy.block_all_tool_calls is True
    for tool in ("bash", "python", "web_search", "read_file"):
        assert tool in policy.disabled_tools
        assert tool in policy.hidden_tools
        assert policy.blocks(tool)


def test_personal_state_sharing_does_not_enable_tools():
    policy = build_effective_tool_policy(
        last_user_message="God I'm feeling a bit ill today."
    )
    assert policy.mode == "conversation_only"
    assert policy.disable_mcp is True
    assert policy.blocks("web_search")
    assert policy.blocks("trigger_research")


def test_explicit_tool_request_in_personal_update_is_preserved():
    message = "I'm feeling ill today. Can you find a nearby pharmacy?"
    assert detect_conversation_only_turn(message) is None
    policy = build_effective_tool_policy(last_user_message=message)
    assert policy.mode == "normal"
    assert not policy.blocks("web_search")


def test_casual_preference_and_joke_requests_enter_conversation_mode():
    preference = (
        "I don't need to be reminded of actual work when I chat or joke; "
        "I need a distraction."
    )
    joke = "LOL, I just want to chat with you. Tell me a good joke."
    assert detect_conversation_only_turn(preference)
    assert detect_conversation_only_turn(joke)
    assert build_effective_tool_policy(last_user_message=joke).blocks("web_search")


def test_short_chat_and_chilling_phrases_enter_conversation_mode():
    for message in (
        "Just chatting",
        "I'm just chilling. Tell me good joke",
        "No, just chat",
        "How are you today?",
    ):
        policy = build_effective_tool_policy(last_user_message=message)
        assert policy.mode == "conversation_only", message
        assert policy.disable_mcp is True


def test_personal_profile_questions_are_not_auto_web_lookups():
    assert not is_factual_lookup_request("How should you format your answers for me?")
    assert not is_factual_lookup_request("What do you know about me?")
    assert is_factual_lookup_request("What is the history of blues?")


def test_music_history_lookup_remains_tool_eligible():
    message = "Yeah, let's look at the history of blues and rock."
    assert detect_conversation_only_turn(message) is None
    assert build_effective_tool_policy(last_user_message=message).mode == "normal"


def test_normal_policy_preserves_existing_disabled_tools():
    policy = build_effective_tool_policy(
        disabled_tools={"web_search"},
        last_user_message="Please check this normally.",
    )
    assert policy.mode == "normal"
    assert policy.blocks("web_search")
    assert not policy.blocks("bash")


def test_web_search_enabled_for_turn_requires_explicit_enable():
    assert web_search_enabled_for_turn(None, None) is False
    assert web_search_enabled_for_turn("true", None) is True
    assert web_search_enabled_for_turn(None, "true") is True
    assert web_search_enabled_for_turn(True, None) is True
    assert web_search_enabled_for_turn("false", "true") is False
    assert web_search_enabled_for_turn(False, "true") is False


def _schema_names(tools):
    return {
        tool.get("function", {}).get("name") or tool.get("name")
        for tool in (tools or [])
    }


def test_agent_loop_web_intent_preserves_disabled_web_tools(monkeypatch):
    _patch_loop_basics(monkeypatch)
    sent_tools = []

    async def _fake_stream(_candidates, messages, **kwargs):
        sent_tools.append(kwargs.get("tools"))
        yield _delta_chunk("ok")
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)

    _collect(
        al.stream_agent_loop(
            "https://api.openai.com/v1",
            "gpt-test",
            [{"role": "user", "content": "please look up the latest CVEs"}],
            max_rounds=1,
            relevant_tools=set(),
            disabled_tools=set(WEB_TOOL_NAMES),
        )
    )

    assert sent_tools
    assert WEB_TOOL_NAMES.isdisjoint(_schema_names(sent_tools[0]))


def test_agent_loop_forced_web_tools_filtered_by_disabled_tools(monkeypatch):
    _patch_loop_basics(monkeypatch)
    sent_tools = []

    async def _fake_stream(_candidates, messages, **kwargs):
        sent_tools.append(kwargs.get("tools"))
        yield _delta_chunk("ok")
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)

    _collect(
        al.stream_agent_loop(
            "https://api.openai.com/v1",
            "gpt-test",
            [{"role": "user", "content": "latest Kubernetes release"}],
            max_rounds=1,
            relevant_tools=set(),
            forced_tools=set(WEB_TOOL_NAMES),
            disabled_tools=set(WEB_TOOL_NAMES),
        )
    )

    assert sent_tools
    assert WEB_TOOL_NAMES.isdisjoint(_schema_names(sent_tools[0]))


def test_agent_loop_policy_blocks_disabled_web_tool_call_before_execution(monkeypatch):
    _patch_loop_basics(monkeypatch)
    called = False

    async def _fake_exec(*args, **kwargs):
        nonlocal called
        called = True
        return ("web_search", {"output": "ran", "exit_code": 0})

    async def _fake_stream(_candidates, messages, **kwargs):
        yield _delta_chunk('```web_search\n{"query":"current CVEs"}\n```')
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "execute_tool_block", _fake_exec, raising=False)
    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)

    policy = build_effective_tool_policy(
        disabled_tools=WEB_TOOL_NAMES,
        last_user_message="please look up the latest CVEs",
    )
    chunks = _collect(
        al.stream_agent_loop(
            "http://local.test/v1",
            "local-model",
            [{"role": "user", "content": "please look up the latest CVEs"}],
            max_rounds=1,
            relevant_tools={"web_search"},
            disabled_tools=set(policy.all_disabled_names()),
            tool_policy=policy,
        )
    )
    events = _events(chunks)
    blocked = [event for event in events if event.get("type") == "tool_output"]

    assert called is False
    assert not any(event.get("type") == "tool_start" for event in events)
    assert blocked
    assert blocked[0]["tool"] == "web_search"
    assert blocked[0]["exit_code"] == 1


def test_executor_policy_backstop_blocks_tools():
    policy = build_effective_tool_policy(last_user_message="Do not use tools.")
    desc, result = asyncio.run(
        execute_tool_block(
            ToolBlock("bash", "echo should-not-run"),
            tool_policy=policy,
            security_context=NO_TOOL_SECURITY_CONTEXT,
        )
    )
    assert desc == "bash: BLOCKED"
    assert result["exit_code"] == 1
    assert "forbade" in result["error"]


def test_agent_loop_blocks_guide_only_fenced_tool_before_start(monkeypatch):
    _patch_loop_basics(monkeypatch)
    called = False

    async def _fake_exec(*args, **kwargs):
        nonlocal called
        called = True
        return ("bash", {"output": "ran", "exit_code": 0})

    async def _fake_stream(_candidates, messages, **kwargs):
        yield _delta_chunk("```bash\necho should-not-run\n```")
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "execute_tool_block", _fake_exec, raising=False)
    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)

    policy = build_effective_tool_policy(last_user_message="GUIDE-ONLY MODE. DO NOT USE TOOLS.")
    chunks = _collect(
        al.stream_agent_loop(
            "http://local.test/v1",
            "local-model",
            [{"role": "user", "content": "GUIDE-ONLY MODE. DO NOT USE TOOLS."}],
            max_rounds=1,
            relevant_tools={"bash"},
            tool_policy=policy,
        )
    )
    events = _events(chunks)
    assert called is False
    assert not any(event.get("type") == "tool_start" for event in events)
    blocked = [event for event in events if event.get("type") == "tool_output"]
    assert blocked
    assert blocked[0]["tool"] == "bash"
    assert blocked[0]["exit_code"] == 1


def test_guide_only_hides_api_function_schemas(monkeypatch):
    _patch_loop_basics(monkeypatch)
    sent_tools = []

    async def _fake_stream(_candidates, messages, **kwargs):
        sent_tools.append(kwargs.get("tools"))
        yield _delta_chunk("ok")
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)
    policy = build_effective_tool_policy(last_user_message="Do not use tools.")

    _collect(
        al.stream_agent_loop(
            "https://api.openai.com/v1",
            "gpt-test",
            [{"role": "user", "content": "Do not use tools."}],
            max_rounds=1,
            relevant_tools={"bash", "web_search"},
            tool_policy=policy,
        )
    )

    assert sent_tools == [None]


def test_guide_only_skips_tool_retrieval(monkeypatch):
    _patch_loop_basics(monkeypatch)
    sent_tools = []

    async def _fake_stream(_candidates, messages, **kwargs):
        sent_tools.append(kwargs.get("tools"))
        yield _delta_chunk("ok")
        yield "data: [DONE]\n\n"

    def _fail_tool_index():
        raise AssertionError("guide-only mode must not retrieve tool candidates")

    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)
    monkeypatch.setitem(
        sys.modules,
        "src.tool_index",
        SimpleNamespace(get_tool_index=_fail_tool_index, ALWAYS_AVAILABLE=set()),
    )
    policy = build_effective_tool_policy(last_user_message="Do not use tools.")

    _collect(
        al.stream_agent_loop(
            "https://api.openai.com/v1",
            "gpt-test",
            [{"role": "user", "content": "Do not use tools."}],
            max_rounds=1,
            relevant_tools=None,
            tool_policy=policy,
        )
    )

    assert sent_tools == [None]


def test_guide_only_blocks_document_prestream(monkeypatch):
    _patch_loop_basics(monkeypatch)

    async def _fake_stream(_candidates, messages, **kwargs):
        yield _delta_chunk("```create_document\nTitle\nmd\nBody\n```")
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)
    policy = build_effective_tool_policy(last_user_message="Do not use tools.")
    chunks = _collect(
        al.stream_agent_loop(
            "http://local.test/v1",
            "local-model",
            [{"role": "user", "content": "Do not use tools."}],
            max_rounds=1,
            relevant_tools={"create_document"},
            tool_policy=policy,
        )
    )
    events = _events(chunks)
    assert not any(event.get("type") == "doc_stream_open" for event in events)
    assert not any(event.get("type") == "tool_start" for event in events)
    assert any(event.get("type") == "tool_output" and event.get("tool") == "create_document" for event in events)


def test_guide_only_blocks_later_round_document_streaming(monkeypatch):
    _patch_loop_basics(monkeypatch)
    calls = 0

    async def _fake_stream(_candidates, messages, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            yield _delta_chunk("```bash\necho blocked\n```")
        else:
            yield _delta_chunk("```create_document\nTitle\nmd\nBody\n```")
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)
    policy = build_effective_tool_policy(last_user_message="Do not use tools.")
    chunks = _collect(
        al.stream_agent_loop(
            "http://local.test/v1",
            "local-model",
            [{"role": "user", "content": "Do not use tools."}],
            max_rounds=2,
            relevant_tools={"bash", "create_document"},
            tool_policy=policy,
        )
    )
    events = _events(chunks)
    assert calls == 2
    assert not any(event.get("type") == "doc_stream_open" for event in events)
    assert not any(event.get("type") == "doc_stream_delta" for event in events)


def test_guide_only_skips_intent_without_action_nudge(monkeypatch):
    _patch_loop_basics(monkeypatch)

    async def _fake_stream(_candidates, messages, **kwargs):
        yield _delta_chunk("I will check the logs.")
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)
    policy = build_effective_tool_policy(last_user_message="Do not use tools.")
    chunks = _collect(
        al.stream_agent_loop(
            "http://local.test/v1",
            "local-model",
            [{"role": "user", "content": "Do not use tools."}],
            max_rounds=2,
            relevant_tools={"bash"},
            tool_policy=policy,
        )
    )
    events = _events(chunks)
    assert not any(event.get("type") == "agent_step" for event in events)


def test_guide_only_suppresses_active_document_context(monkeypatch):
    _patch_loop_basics(monkeypatch)
    prompt_payloads = []

    async def _fake_stream(_candidates, messages, **kwargs):
        prompt_payloads.append("\n\n".join(str(msg.get("content", "")) for msg in messages))
        yield _delta_chunk("ok")
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)
    policy = build_effective_tool_policy(last_user_message="Do not use tools.")
    active_doc = SimpleNamespace(
        id="doc-1",
        current_content="SECRET ACTIVE DOCUMENT CONTENT",
        title="Secret Doc",
        language="markdown",
    )

    _collect(
        al.stream_agent_loop(
            "http://local.test/v1",
            "local-model",
            [{"role": "user", "content": "Do not use tools."}],
            max_rounds=1,
            relevant_tools={"edit_document"},
            tool_policy=policy,
            active_document=active_doc,
        )
    )

    assert prompt_payloads
    assert "SECRET ACTIVE DOCUMENT CONTENT" not in prompt_payloads[0]
    assert "ACTIVE DOCUMENT" not in prompt_payloads[0]
    assert "Relevant skills" not in prompt_payloads[0]


def test_document_my_style_does_not_infer_public_persona(monkeypatch):
    _patch_loop_basics(monkeypatch)
    monkeypatch.setattr(al, "_build_base_prompt", lambda *a, **k: ("BASE", ""), raising=False)
    monkeypatch.setattr(al, "_cached_base_prompt", None, raising=False)
    monkeypatch.setattr(al, "_cached_base_prompt_key", None, raising=False)

    import src.settings as settings
    monkeypatch.setattr(settings, "load_settings", lambda: {"document_writing_style": ""}, raising=False)

    active_doc = SimpleNamespace(
        id="doc-style",
        current_content="A short poem already exists here.",
        title="Morning Poem",
        language="markdown",
    )

    messages, _ = al._build_system_prompt(
        [{"role": "user", "content": "Write as my style"}],
        model="local-model",
        active_document=active_doc,
        mcp_mgr=None,
        relevant_tools={"edit_document", "update_document"},
        suppress_skills=True,
    )
    payload = "\n\n".join(str(msg.get("content", "")) for msg in messages)

    assert "There is no saved document writing style" in payload
    assert "do NOT infer that style from memories, identity, public persona" in payload


def test_guide_only_skips_teacher_escalation(monkeypatch):
    _patch_loop_basics(monkeypatch)

    async def _fake_stream(_candidates, messages, **kwargs):
        yield _delta_chunk("Could you tell me what output you see?")
        yield "data: [DONE]\n\n"

    async def _fail_teacher(*_args, **_kwargs):
        raise AssertionError("teacher escalation must not run in guide-only mode")
        yield ""

    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)
    monkeypatch.setitem(
        sys.modules,
        "src.teacher_escalation",
        SimpleNamespace(run_teacher_inline=_fail_teacher),
    )
    policy = build_effective_tool_policy(last_user_message="Do not use tools.")

    chunks = _collect(
        al.stream_agent_loop(
            "http://local.test/v1",
            "local-model",
            [{"role": "user", "content": "Do not use tools."}],
            max_rounds=1,
            relevant_tools={"bash"},
            tool_policy=policy,
        )
    )

    assert any("Could you tell me" in chunk for chunk in chunks)
