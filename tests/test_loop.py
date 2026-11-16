"""1단계: 에이전트 루프. 실제 LLM 대신 FakeLLM으로 정해진 응답을 차례로 돌려준다."""

from decimal import Decimal

import pytest

from agent.llm import FakeLLM, LLMError, LLMResponse, ToolCall

pytestmark = pytest.mark.django_db


# ── 테스트 도우미 ───────────────────────────────────────


def answer(text="완료했습니다."):
    return LLMResponse(text=text, tool_calls=[], stop_reason="end_turn", input_tokens=100, output_tokens=20)


def call(name, tool_input, call_id="toolu_1"):
    return LLMResponse(
        text="",
        tool_calls=[ToolCall(id=call_id, name=name, input=tool_input)],
        stop_reason="tool_use",
        input_tokens=100,
        output_tokens=20,
    )


@pytest.fixture
def chat_task(user, conversation):
    """사용자가 메시지를 보내 Task가 막 만들어진 상태."""
    from agent.models import Task
    from chat.models import Message

    message = Message.objects.create(
        conversation=conversation, role=Message.Role.USER, content="우유 사기 추가해줘"
    )
    task = Task.objects.create(user=user, conversation=conversation, trigger_message=message)
    message.task = task
    message.save()
    return task


def run(task, responses):
    from agent.loop import run_task

    llm = FakeLLM(responses)
    run_task(task.id, llm=llm)
    task.refresh_from_db()
    return llm


def tool_results_sent_in(llm_call):
    """LLM에 보낸 messages에서 tool_result 블록만 모은다."""
    blocks = []
    for message in llm_call["messages"]:
        if isinstance(message["content"], list):
            blocks += [b for b in message["content"] if b.get("type") == "tool_result"]
    return blocks


# ── 명세의 필수 케이스 ──────────────────────────────────


def test_direct_answer_finishes_task_with_one_message(chat_task):
    from chat.models import Message

    run(chat_task, [answer("안녕하세요!")])

    assert chat_task.status == "done"
    assert chat_task.final_answer == "안녕하세요!"
    replies = Message.objects.filter(task=chat_task, role=Message.Role.ASSISTANT)
    assert replies.count() == 1


def test_one_tool_call_then_answer_records_one_ok_step(chat_task, user):
    from tools.models import Todo

    run(chat_task, [call("add_todo", {"title": "우유 사기"}), answer()])

    steps = list(chat_task.steps.all())
    assert len(steps) == 1
    assert steps[0].ok is True
    assert steps[0].status == "done"
    assert steps[0].risk_level == "INTERNAL"
    assert Todo.objects.filter(user=user, title="우유 사기").exists()
    assert chat_task.status == "done"


def test_unknown_tool_does_not_crash_and_error_goes_back_to_llm(chat_task):
    llm = run(chat_task, [call("buy_ticket", {"to": "부산"}), answer("그건 할 수 없어요.")])

    assert chat_task.status == "done"
    step = chat_task.steps.get()
    assert step.ok is False
    assert step.status == "failed"
    result = tool_results_sent_in(llm.calls[1])[0]
    assert result["is_error"] is True
    assert "buy_ticket" in result["content"]


def test_invalid_input_is_not_executed_and_error_goes_back(chat_task, user):
    from tools.models import Todo

    llm = run(chat_task, [call("add_todo", {"title": 123}), answer()])

    assert not Todo.objects.filter(user=user).exists()
    assert chat_task.steps.get().ok is False
    assert tool_results_sent_in(llm.calls[1])[0]["is_error"] is True


def test_thirteenth_step_ends_with_budget_exceeded(chat_task):
    from chat.models import Message

    responses = [call("list_todos", {}, call_id=f"toolu_{i}") for i in range(13)]
    llm = run(chat_task, responses)

    assert chat_task.status == "budget_exceeded"
    assert chat_task.steps.count() == 12
    assert len(llm.calls) == 12  # 13번째 LLM 호출 전에 멈춘다
    stop_message = Message.objects.get(task=chat_task, role=Message.Role.ASSISTANT)
    assert "멈" in stop_message.content


# ── 그 밖의 루프 규칙 ───────────────────────────────────


def test_daily_cost_limit_also_ends_with_budget_exceeded(chat_task, settings):
    from agent.models import UsageLog

    settings.DAILY_COST_LIMIT_USD = Decimal("0.01")
    UsageLog.objects.create(purpose="agent", model="x", cost_usd=Decimal("0.02"))

    llm = run(chat_task, [answer()])

    assert chat_task.status == "budget_exceeded"
    assert llm.calls == []


def test_usage_is_logged_for_every_llm_call(chat_task, settings):
    from agent.models import UsageLog

    settings.MODEL_PRICES = {settings.AGENT_MODEL_FAST: {"input": Decimal("1"), "output": Decimal("10")}}
    run(chat_task, [call("list_todos", {}), answer()])

    logs = UsageLog.objects.filter(task=chat_task)
    assert logs.count() == 2
    # 입력 100토큰 x $1/1M + 출력 20토큰 x $10/1M = $0.0003
    assert logs.first().cost_usd == Decimal("0.000300")
    assert chat_task.total_tokens == 240


def test_switches_to_smart_model_after_four_steps(chat_task, settings):
    responses = [call("list_todos", {}, call_id=f"toolu_{i}") for i in range(5)] + [answer()]
    llm = run(chat_task, responses)

    models = [c["model"] for c in llm.calls]
    assert models[:5] == [settings.AGENT_MODEL_FAST] * 5
    assert models[5] == settings.AGENT_MODEL_SMART


def test_llm_error_is_retried_twice_then_task_fails(chat_task, monkeypatch):
    from agent import loop
    from chat.models import Message

    monkeypatch.setattr(loop.time, "sleep", lambda seconds: None)
    llm = run(chat_task, [LLMError("일시 오류")] * 3)

    assert len(llm.calls) == 3
    assert chat_task.status == "failed"
    assert Message.objects.filter(task=chat_task, role=Message.Role.ASSISTANT).exists()


def test_llm_recovers_after_one_retry(chat_task, monkeypatch):
    from agent import loop

    monkeypatch.setattr(loop.time, "sleep", lambda seconds: None)
    run(chat_task, [LLMError("일시 오류"), answer()])

    assert chat_task.status == "done"


def test_refusal_is_reported_as_failure(chat_task):
    refusal = LLMResponse(text="", tool_calls=[], stop_reason="refusal", input_tokens=10, output_tokens=0)
    run(chat_task, [refusal])

    assert chat_task.status == "failed"


def test_finished_task_is_not_run_again(chat_task):
    run(chat_task, [answer()])
    llm = run(chat_task, [answer("또 실행됨")])

    assert llm.calls == []
    assert chat_task.final_answer == "완료했습니다."


def test_schedule_task_receives_only_read_tools(user, conversation):
    from agent.models import Task

    task = Task.objects.create(user=user, conversation=conversation, origin=Task.Origin.SCHEDULE)
    llm = run(task, [answer()])

    tool_names = {t["name"] for t in llm.calls[0]["tools"]}
    assert "list_todos" in tool_names
    assert "add_todo" not in tool_names


def test_prompt_contains_history_and_seoul_date(chat_task):
    llm = run(chat_task, [answer()])

    sent = llm.calls[0]
    assert sent["messages"][-1] == {"role": "user", "content": "우유 사기 추가해줘"}
    assert "Asia/Seoul" in sent["system"]


def test_step_pairs_are_replayed_as_tool_use_and_tool_result(chat_task):
    llm = run(chat_task, [call("list_todos", {}, call_id="toolu_A"), answer()])

    second = llm.calls[1]["messages"]
    assert second[-2]["role"] == "assistant"
    assert second[-2]["content"][0] == {
        "type": "tool_use",
        "id": "toolu_A",
        "name": "list_todos",
        "input": {},
    }
    assert second[-1]["content"][0]["tool_use_id"] == "toolu_A"
