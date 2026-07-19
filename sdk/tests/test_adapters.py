from chatlog_sdk import AnthropicAdapter, OpenAIAdapter, TokenUsage


def test_openai_adapter_extracts_chat_completion() -> None:
    response = {
        "model": "gpt-4.1-mini",
        "usage": {"prompt_tokens": 12, "completion_tokens": 7},
        "choices": [{"message": {"content": "Hello"}}],
    }
    adapter = OpenAIAdapter()

    assert adapter.matches(response)
    assert adapter.extract_model(response) == "gpt-4.1-mini"
    assert adapter.extract_tokens(response) == TokenUsage(input_tokens=12, output_tokens=7)
    assert adapter.extract_text(response) == "Hello"


def test_anthropic_adapter_extracts_message() -> None:
    response = {
        "model": "claude-sonnet-4",
        "usage": {"input_tokens": 15, "output_tokens": 9},
        "content": [{"type": "text", "text": "Hi "}, {"type": "text", "text": "there"}],
    }
    adapter = AnthropicAdapter()

    assert adapter.matches(response)
    assert adapter.extract_model(response) == "claude-sonnet-4"
    assert adapter.extract_tokens(response) == TokenUsage(input_tokens=15, output_tokens=9)
    assert adapter.extract_text(response) == "Hi there"
