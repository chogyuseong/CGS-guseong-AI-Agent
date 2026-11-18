"""1단계 에이전트 실행 테스트 (SPEC 1단계 블록 2). 실제 모델 대신 FunctionModel 대본을 쓴다."""

import httpx
from pydantic import SecretStr
from pydantic_ai.messages import ToolCallPart, ToolReturnPart
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.agent import MyAgent
from app.agent.guard import GuardAgent
from app.agent.runner import RunServices, run_task
from app.config import Settings
from app.models import Conversation, Step, Task, Todo, UsageLog, User
from app.models.enums import TaskOrigin
from app.push import PushNotifier
from tests.conftest import TEST_PAYLOAD_KEY, message_body
from tests.fake_push import FakePushSender
from tests.model_scripts import call, scripted, text

# 등급별 도구 이름(4단계에서 실제 도구가 더해졌다). 예약·감시 task는 READ만, chat task는 세 등급 모두를 받는다.
READ_TOOL_NAMES = sorted(
    [
        "get_weather",
        "list_events",
        "list_memories",
        "list_notes",
        "list_todos",
        "search_places",
        "web_search",
        "list_schedules",  # 6단계
        "list_watches",  # 6단계
    ]
)
INTERNAL_TOOL_NAMES = sorted(
    [
        "add_todo",
        "complete_todo",
        "forget",
        "remember",
        "save_note",
        "create_schedule",  # 6단계
        "delete_schedule",  # 6단계
        "create_watch",  # 6단계
        "delete_watch",  # 6단계
        "draft_message",  # 4단계 보강: 초안만(발송 없음)
    ]
)
EXTERNAL_TOOL_NAMES = sorted(["create_event", "delete_event", "update_event"])
# 7단계: 메일 도구는 READ지만 chat에만 넘긴다(예약·감시 task에는 없다). app/agent/toolsets.py의 mail_tools.
MAIL_TOOL_NAMES = sorted(["list_mail", "read_mail"])


async def send(client: httpx.AsyncClient, conversation_id: int, content: str) -> dict:
    """메시지를 보내고 task 상세를 돌려준다."""
    response = await client.post(f"/api/conversations/{conversation_id}/messages", json=message_body(content))
    assert response.status_code == 202, response.text
    task_id = response.json()["task_id"]
    detail = await client.get(f"/api/tasks/{task_id}")
    assert detail.status_code == 200
    return detail.json()


async def test_direct_answer_without_tools(client: httpx.AsyncClient, agent: MyAgent, conversation: int) -> None:
    model = scripted(text("안녕하세요! 무엇을 도와드릴까요?"))

    with agent.override(model=model.model()):
        task = await send(client, conversation, "안녕")

    assert task["status"] == "done"
    assert task["final_answer"] == "안녕하세요! 무엇을 도와드릴까요?"
    assert task["steps"] == []
    messages = (await client.get(f"/api/conversations/{conversation}/messages")).json()
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[1]["content"] == "안녕하세요! 무엇을 도와드릴까요?"
    assert messages[1]["task_id"] == task["id"]


async def test_add_todo_creates_step_todo_usage_and_answer(
    client: httpx.AsyncClient, agent: MyAgent, conversation: int, db_session: AsyncSession, logged_in_user: User
) -> None:
    model = scripted(call("add_todo", title="우유 사기"), text("할 일에 '우유 사기'를 추가했습니다."))

    with agent.override(model=model.model()):
        task = await send(client, conversation, "우유 사기 할 일에 추가해줘")

    assert task["status"] == "done"
    assert len(task["steps"]) == 1
    step = task["steps"][0]
    assert step["tool_name"] == "add_todo"
    assert step["ok"] is True
    assert step["status"] == "done"
    assert step["risk_level"] == "INTERNAL"
    assert step["label"] == "할 일에 '우유 사기' 추가하는 중"
    assert step["order"] == 1
    assert step["duration_ms"] is not None
    assert step["output"]["status"] == "succeeded"
    todos = (await db_session.scalars(select(Todo).where(Todo.user_id == logged_in_user.id))).all()
    assert [t.title for t in todos] == ["우유 사기"]
    usage_rows = (await db_session.scalars(select(UsageLog).where(UsageLog.task_id == task["id"]))).all()
    assert len(usage_rows) == 2  # 모델 호출 2번(도구 호출, 최종 답변)
    assert all(row.user_id == logged_in_user.id for row in usage_rows)
    messages = (await client.get(f"/api/conversations/{conversation}/messages")).json()
    assert [m["role"] for m in messages] == ["user", "assistant"]


async def test_unknown_tool_and_bad_arguments_do_not_crash(
    client: httpx.AsyncClient, agent: MyAgent, conversation: int
) -> None:
    """테스트 8: 없는 도구 이름이나 잘못된 인자로 불러도 서버가 죽지 않는다."""
    model = scripted(
        call("delete_everything"),
        call("add_todo", wrong_field=123),
        text("요청을 처리하지 못했습니다."),
    )

    with agent.override(model=model.model()):
        task = await send(client, conversation, "이상한 요청")

    assert task["status"] in {"done", "failed"}
    assert task["steps"] == []  # 실행된 도구가 없다
    health = await client.get("/api/health")
    assert health.status_code == 200


async def test_model_that_keeps_calling_unknown_tool_fails_task_but_server_survives(
    client: httpx.AsyncClient, agent: MyAgent, conversation: int
) -> None:
    model = scripted(call("delete_everything"))  # 끝까지 없는 도구만 부른다

    with agent.override(model=model.model()):
        task = await send(client, conversation, "계속 이상한 요청")

    assert task["status"] == "failed"
    assert task["error"]
    messages = (await client.get(f"/api/conversations/{conversation}/messages")).json()
    assert messages[-1]["role"] == "assistant"  # 실패도 채팅에 알린다


async def test_unexpected_tool_exception_becomes_failed_result(
    client: httpx.AsyncClient, agent: MyAgent, conversation: int, monkeypatch
) -> None:
    """도구 안에서 예상 못 한 예외가 나도 한 곳(StepTracking)에서 ok=false로 바뀐다."""

    async def broken(*args: object, **kwargs: object) -> None:
        raise RuntimeError("DB가 갑자기 끊김")

    monkeypatch.setattr("app.tools.notes.insert_todo", broken)
    model = scripted(call("add_todo", title="우유 사기"), text("할 일을 추가하지 못했습니다."))

    with agent.override(model=model.model()):
        task = await send(client, conversation, "우유 사기 할 일에 추가해줘")

    assert task["status"] == "done"
    step = task["steps"][0]
    assert step["ok"] is False
    assert step["status"] == "failed"
    assert "DB가 갑자기 끊김" not in step["output"]["user_message"]  # 내부 오류 내용은 모델과 화면에 보내지 않는다


async def test_schedule_task_gets_read_tools_only(
    agent: MyAgent,
    guard: GuardAgent,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    test_settings: Settings,
    logged_in_user: User,
) -> None:
    """테스트 5: origin=schedule인 task는 모델이 받는 도구 목록에 read_tools만 있다."""
    conversation = Conversation(user_id=logged_in_user.id, title="예약 작업")
    db_session.add(conversation)
    await db_session.flush()
    task = Task(conversation_id=conversation.id, user_id=logged_in_user.id, origin=TaskOrigin.SCHEDULE)
    db_session.add(task)
    await db_session.commit()
    model = scripted(text("오늘 할 일은 없습니다."))
    notifier = PushNotifier(session_factory, test_settings, FakePushSender())
    services = RunServices(
        agent=agent,
        guard=guard,
        session_factory=session_factory,
        settings=test_settings,
        notifier=notifier,
        http=httpx.AsyncClient(),
    )

    with agent.override(model=model.model()):
        await run_task(services, task.id, prompt="오늘 할 일 요약해줘")

    assert model.seen_tool_names == [READ_TOOL_NAMES]


async def test_chat_task_gets_all_tool_levels(
    client: httpx.AsyncClient, agent: MyAgent, conversation: int, test_settings: Settings
) -> None:
    # 7단계 보강: 메일 도구는 메일 기능 플래그(PAYLOAD_ENCRYPTION_KEY)가 있을 때만 보인다(test_mail_privacy.py).
    test_settings.payload_encryption_key = SecretStr(TEST_PAYLOAD_KEY)
    model = scripted(text("네"))

    with agent.override(model=model.model()):
        await send(client, conversation, "안녕")

    assert model.seen_tool_names == [
        sorted([*READ_TOOL_NAMES, *MAIL_TOOL_NAMES, *INTERNAL_TOOL_NAMES, *EXTERNAL_TOOL_NAMES, "compare_products"])
    ]


async def test_next_task_receives_previous_tool_calls(
    client: httpx.AsyncClient, agent: MyAgent, conversation: int
) -> None:
    """새 task는 같은 대화의 이전 task가 남긴 도구 호출과 결과를 이어받는다."""
    first = scripted(call("add_todo", title="우유 사기"), text("추가했습니다."))
    with agent.override(model=first.model()):
        await send(client, conversation, "우유 사기 할 일에 추가해줘")

    second = scripted(text("이미 추가되어 있습니다."))
    with agent.override(model=second.model()):
        await send(client, conversation, "아까 뭐 했어?")

    seen = second.seen_messages[0]
    tool_calls = [p for m in seen for p in m.parts if isinstance(p, ToolCallPart)]
    tool_returns = [p for m in seen for p in m.parts if isinstance(p, ToolReturnPart)]
    assert [c.tool_name for c in tool_calls] == ["add_todo"]
    assert [r.tool_call_id for r in tool_returns] == [tool_calls[0].tool_call_id]  # 호출과 결과가 짝으로 넘어온다


async def test_steps_count_matches_db(
    client: httpx.AsyncClient, agent: MyAgent, conversation: int, db_session: AsyncSession
) -> None:
    model = scripted(call("add_todo", title="A"), call("list_todos"), text("완료"))

    with agent.override(model=model.model()):
        task = await send(client, conversation, "A 추가하고 목록 보여줘")

    assert [s["order"] for s in task["steps"]] == [1, 2]
    assert [s["risk_level"] for s in task["steps"]] == ["INTERNAL", "READ"]
    count = await db_session.scalar(select(func.count()).select_from(Step).where(Step.task_id == task["id"]))
    assert count == 2
