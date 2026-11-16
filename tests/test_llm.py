"""1단계: 실제 Claude 클라이언트. 네트워크 없이 확인할 수 있는 부분만 검사한다."""

import anthropic
import pytest

from agent.llm import AnthropicClient, LLMNotConfigured, _to_llm_response, with_cache_breakpoints


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
    assert (response.cache_write_tokens, response.cache_read_tokens) == (0, 0)


def test_cache_tokens_are_read_from_usage():
    message = anthropic.types.Message.model_validate(
        {
            "id": "msg_2",
            "type": "message",
            "role": "assistant",
            "model": "claude-haiku-5-5",
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "content": [{"type": "text", "text": "네"}],
            "usage": {
                "input_tokens": 50,
                "output_tokens": 5,
                "cache_creation_input_tokens": 1200,
                "cache_read_input_tokens": 3400,
            },
        }
    )
    response = _to_llm_response(message)

    assert (response.cache_write_tokens, response.cache_read_tokens) == (1200, 3400)


# ── 프롬프트 캐싱 ───────────────────────────────────────


def test_cache_breakpoints_on_system_and_last_block():
    messages = [
        {"role": "user", "content": "우유 사기 추가해줘"},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "add_todo", "input": {}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "{}"}]},
    ]
    system, cached = with_cache_breakpoints("시스템 프롬프트", messages)

    # 1) 시스템 블록 끝: 도구 목록 + 시스템 프롬프트를 함께 캐시한다(여러 Task가 공유)
    assert system == [{"type": "text", "text": "시스템 프롬프트", "cache_control": {"type": "ephemeral"}}]
    # 2) 마지막 메시지의 마지막 블록: 같은 Task의 다음 호출이 지금까지의 대화를 다시 읽는다
    assert cached[-1]["content"][-1]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in cached[0]
    # 원본은 바꾸지 않는다(루프가 같은 목록을 다시 쓸 수 있게)
    assert "cache_control" not in messages[-1]["content"][-1]


def test_string_content_is_turned_into_a_text_block_for_the_marker():
    _, cached = with_cache_breakpoints("s", [{"role": "user", "content": "안녕"}])

    assert cached[-1]["content"] == [{"type": "text", "text": "안녕", "cache_control": {"type": "ephemeral"}}]


def test_missing_cache_price_blocks_paid_call(settings):
    settings.ANTHROPIC_API_KEY = "sk-ant-test-not-real"
    settings.MODEL_PRICES = {"claude-haiku-5-5": {"input": 1, "output": 1}}  # 캐시 가격 없음
    with pytest.raises(LLMNotConfigured, match="MODEL_PRICES"):
        AnthropicClient().complete(system="s", messages=[], tools=[], model="claude-haiku-5-5")
