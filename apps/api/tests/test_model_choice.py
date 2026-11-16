"""FAST로 시작하고 이번 task의 도구 호출이 4번을 넘으면 SMART로 바꾸는 규칙 테스트."""

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    UserPromptPart,
)

from app.agent.model_choice import SWITCH_AFTER_TOOL_CALLS, choose_model_name, count_tool_calls_in_current_run

FAST = "anthropic:fast-model"
SMART = "anthropic:smart-model"


def responses_with_tool_calls(count: int) -> list[ModelMessage]:
    return [ModelResponse(parts=[ToolCallPart("list_todos", {}, tool_call_id=f"c{i}")]) for i in range(count)]


def test_switch_threshold_is_four() -> None:
    assert SWITCH_AFTER_TOOL_CALLS == 4


def test_fast_until_four_tool_calls_then_smart() -> None:
    assert choose_model_name(0, fast=FAST, smart=SMART) == FAST
    assert choose_model_name(4, fast=FAST, smart=SMART) == FAST
    assert choose_model_name(5, fast=FAST, smart=SMART) == SMART


def test_counts_only_tool_calls_after_latest_user_prompt() -> None:
    previous_task: list[ModelMessage] = [
        ModelRequest(parts=[UserPromptPart("이전 요청")]),
        *responses_with_tool_calls(6),
        ModelResponse(parts=[TextPart("이전 답")]),
    ]
    current_task: list[ModelMessage] = [
        ModelRequest(parts=[UserPromptPart("이번 요청")]),
        *responses_with_tool_calls(2),
    ]

    assert count_tool_calls_in_current_run(previous_task + current_task) == 2


def test_counts_parallel_tool_calls_in_one_response() -> None:
    messages: list[ModelMessage] = [
        ModelRequest(parts=[UserPromptPart("요청")]),
        ModelResponse(parts=[ToolCallPart("a", {}, tool_call_id="1"), ToolCallPart("b", {}, tool_call_id="2")]),
    ]

    assert count_tool_calls_in_current_run(messages) == 2
