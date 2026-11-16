"""이전 task 기록을 이어받을 때의 자르기 규칙 테스트."""

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from app.agent.history import (
    build_history,
    dump_messages,
    load_messages,
    without_unfinished_tail,
)


def task_messages(prompt: str, *, with_tool: bool = True, answer_size: int = 10) -> list[ModelMessage]:
    """task 하나의 기록: 사용자 요청 → (도구 호출 → 도구 결과) → 최종 답변."""
    messages: list[ModelMessage] = [ModelRequest(parts=[UserPromptPart(prompt)])]
    if with_tool:
        messages.append(ModelResponse(parts=[ToolCallPart("add_todo", {"title": prompt}, tool_call_id=f"c-{prompt}")]))
        messages.append(ModelRequest(parts=[ToolReturnPart("add_todo", {"ok": True}, tool_call_id=f"c-{prompt}")]))
    messages.append(ModelResponse(parts=[TextPart("답" * answer_size)]))
    return messages


def prompts(messages: list[ModelMessage]) -> list[str]:
    return [str(p.content) for m in messages for p in m.parts if isinstance(p, UserPromptPart)]


def test_keeps_whole_tasks_in_order() -> None:
    history = build_history([task_messages("1"), task_messages("2")], max_tasks=10, max_chars=100_000)

    assert prompts(history) == ["1", "2"]
    assert len(history) == 8


def test_drops_oldest_tasks_beyond_max_tasks() -> None:
    tasks = [task_messages(str(i)) for i in range(5)]

    history = build_history(tasks, max_tasks=2, max_chars=100_000)

    assert prompts(history) == ["3", "4"]


def test_drops_oldest_tasks_beyond_char_budget_without_splitting_tool_pairs() -> None:
    tasks = [task_messages("old", answer_size=5000), task_messages("new", answer_size=10)]

    history = build_history(tasks, max_tasks=10, max_chars=3000)

    assert prompts(history) == ["new"]
    calls = [p.tool_call_id for m in history for p in m.parts if isinstance(p, ToolCallPart)]
    returns = [p.tool_call_id for m in history for p in m.parts if isinstance(p, ToolReturnPart)]
    assert calls == returns


def test_single_task_over_budget_is_dropped_entirely() -> None:
    history = build_history([task_messages("huge", answer_size=10_000)], max_tasks=10, max_chars=1000)

    assert history == []


def test_unfinished_tail_is_removed() -> None:
    """실패한 run이 도구 호출만 하고 결과 없이 끝나면 그 호출을 기록에서 뺀다."""
    messages = task_messages("x", with_tool=False)[:1]
    messages.append(ModelResponse(parts=[ToolCallPart("add_todo", {"title": "x"}, tool_call_id="dangling")]))

    cleaned = without_unfinished_tail(messages)

    assert all(not isinstance(p, ToolCallPart) for m in cleaned for p in m.parts)


def test_dump_and_load_round_trip() -> None:
    messages = task_messages("왕복")

    restored = load_messages(dump_messages(messages))

    assert prompts(restored) == ["왕복"]
    assert len(restored) == len(messages)
