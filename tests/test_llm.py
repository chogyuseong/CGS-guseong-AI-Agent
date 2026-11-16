"""1단계: 실제 Claude 클라이언트. 네트워크 없이 확인할 수 있는 부분만 검사한다."""

import anthropic
import pytest

from agent.llm import AnthropicClient, LLMNotConfigured, _to_llm_response


def test_missing_api_key_blocks_client(settings):
    settings.ANTHROPIC_API_KEY = ""
    with pytest.raises(LLMNotConfigured, match="API 키"):
        AnthropicClient()


def test_unknown_price_blocks_paid_call_before_network(settings):
    settings.ANTHROPIC_API_KEY = "sk-ant-test-not-real"
    settings.MODEL_PRICES = {"claude-haiku-5-5": {"input": None, "output": None}}
    client = AnthropicClient()
    with pytest.raises(LLMNotConfigured, match="MODEL_PRICES"):
        client.complete(
            system="s", messages=[{"role": "user", "content": "hi"}], tools=[], model="claude-haiku-5-5"
        )


def test_sdk_message_is_converted_without_thinking():
    message = anthropic.types.Message.model_validate(
        {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "model": "claude-haiku-5-5",
            "stop_reason": "tool_use",
            "stop_sequence": None,
            "content": [
                {"type": "thinking", "thinking": "", "signature": "sig"},
                {"type": "text", "text": "할 일을 추가할게요."},
                {"type": "tool_use", "id": "toolu_1", "name": "add_todo", "input": {"title": "우유 사기"}},
            ],
            "usage": {"input_tokens": 120, "output_tokens": 30},
        }
    )
    response = _to_llm_response(message)

    assert response.text == "할 일을 추가할게요."
    assert response.stop_reason == "tool_use"
    assert response.tool_calls[0].name == "add_todo"
    assert response.tool_calls[0].input == {"title": "우유 사기"}
    assert (response.input_tokens, response.output_tokens) == (120, 30)
