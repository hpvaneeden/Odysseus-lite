from odysseus_extensions.social_skill import (
    compact_social_history,
    is_explicit_request,
)


def test_short_questions_and_information_requests_are_not_social_filler():
    assert is_explicit_request("Hey, therapist?")
    assert is_explicit_request("Hi, can you find local support resources?")
    assert is_explicit_request("Hey, latest USD/ZAR rate")


def test_plain_smalltalk_and_emotional_openings_are_not_requests():
    assert not is_explicit_request("What's up?")
    assert not is_explicit_request("Hey, I'm depressed")


def test_compacts_only_completed_standalone_greeting_pairs():
    messages = [
        {"role": "user", "content": "Hello!"},
        {"role": "assistant", "content": "Hi! How can I help you today?"},
        {"role": "user", "content": "Explain compound interest."},
    ]

    compacted = compact_social_history(messages)

    assert compacted == [messages[-1]]
    assert len(messages) == 3


def test_preserves_current_greeting_and_mixed_request():
    messages = [
        {"role": "user", "content": "Hi"},
        {"role": "assistant", "content": "Hello!"},
        {"role": "user", "content": "Hey, can you find local support resources?"},
    ]

    assert compact_social_history(messages) == [messages[-1]]


def test_preserves_emotional_support_history():
    messages = [
        {"role": "user", "content": "I feel depressed and could use a friend."},
        {"role": "assistant", "content": "I'm sorry you're dealing with that. I'm here with you."},
        {"role": "user", "content": "Can we talk for a bit?"},
    ]

    assert compact_social_history(messages) == messages


def test_preserves_assistant_turn_with_tool_calls():
    messages = [
        {"role": "user", "content": "Hi"},
        {
            "role": "assistant",
            "content": "Hello!",
            "tool_calls": [{"id": "call_1"}],
        },
        {"role": "user", "content": "What is the weather?"},
    ]

    assert compact_social_history(messages) == messages


def test_preserves_non_string_multimodal_messages():
    messages = [
        {"role": "user", "content": [{"type": "text", "text": "Hi"}]},
        {"role": "assistant", "content": "Hello!"},
        {"role": "user", "content": "What is in this image?"},
    ]

    assert compact_social_history(messages) == messages