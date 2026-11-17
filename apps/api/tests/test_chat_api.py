"""1단계 API 모양 테스트: 대화 만들기, 메시지 전송, 작업 조회, 소유권, 전송 제한."""

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.agent import MyAgent
from app.models import Conversation, Task
from app.models.enums import TaskOrigin
from app.rate_limit import SlidingWindowRateLimiter
from tests.conftest import UserFactory, message_body
from tests.model_scripts import call, scripted, text
from tests.test_agent_run import send


async def test_create_conversation_returns_201(client: httpx.AsyncClient, logged_in_user) -> None:
    created = await client.post("/api/conversations", json={"title": "주말 계획"})
    default = await client.post("/api/conversations", json={})

    assert created.status_code == 201
    assert created.json()["title"] == "주말 계획"
    assert default.status_code == 201
    assert default.json()["title"] == "새 대화"
    listing = (await client.get("/api/conversations")).json()
    assert {c["id"] for c in listing} == {created.json()["id"], default.json()["id"]}


async def test_create_conversation_requires_login(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/conversations", json={})

    assert response.status_code == 401


async def test_send_message_returns_202_with_task_id(
    client: httpx.AsyncClient, agent: MyAgent, conversation: int
) -> None:
    with agent.override(model=scripted(text("네")).model()):
        response = await client.post(f"/api/conversations/{conversation}/messages", json=message_body("안녕"))

    assert response.status_code == 202
    assert set(response.json()) == {"task_id"}


async def test_empty_message_is_rejected(client: httpx.AsyncClient, conversation: int) -> None:
    response = await client.post(f"/api/conversations/{conversation}/messages", json=message_body("   "))

    assert response.status_code == 422


async def test_cannot_send_to_other_users_conversation(
    client: httpx.AsyncClient, db_session: AsyncSession, make_user: UserFactory, logged_in_user
) -> None:
    other = await make_user("other@example.com", "password-other")
    other_conversation = Conversation(user_id=other.id, title="남의 대화")
    db_session.add(other_conversation)
    await db_session.commit()

    response = await client.post(f"/api/conversations/{other_conversation.id}/messages", json=message_body("안녕"))

    assert response.status_code == 404


async def test_task_detail_shape(client: httpx.AsyncClient, agent: MyAgent, conversation: int) -> None:
    with agent.override(model=scripted(call("add_todo", title="우유 사기"), text("추가했습니다.")).model()):
        task = await send(client, conversation, "우유 사기 할 일에 추가해줘")

    assert set(task) == {
        "id",
        "conversation_id",
        "origin",
        "status",
        "final_answer",
        "error",
        "request_count",
        "total_tokens",
        "created_at",
        "updated_at",
        "steps",
        "approvals",
    }
    assert task["approvals"] == []
    assert task["origin"] == "chat"
    assert task["request_count"] == 2
    assert set(task["steps"][0]) == {
        "id",
        "order",
        "tool_name",
        "label",
        "risk_level",
        "status",
        "ok",
        "input",
        "output",
        "duration_ms",
    }
    assert task["steps"][0]["input"] == {"title": "우유 사기"}


async def test_cannot_read_other_users_task(
    client: httpx.AsyncClient, db_session: AsyncSession, make_user: UserFactory, logged_in_user
) -> None:
    other = await make_user("other@example.com", "password-other")
    other_conversation = Conversation(user_id=other.id, title="남의 대화")
    db_session.add(other_conversation)
    await db_session.flush()
    other_task = Task(conversation_id=other_conversation.id, user_id=other.id, origin=TaskOrigin.CHAT)
    db_session.add(other_task)
    await db_session.commit()

    response = await client.get(f"/api/tasks/{other_task.id}")
    listing = await client.get("/api/tasks")

    assert response.status_code == 404
    assert listing.json() == []


async def test_list_tasks_newest_first(client: httpx.AsyncClient, agent: MyAgent, conversation: int) -> None:
    with agent.override(model=scripted(text("하나")).model()):
        first = await send(client, conversation, "첫 번째")
    with agent.override(model=scripted(text("둘")).model()):
        second = await send(client, conversation, "두 번째")

    listing = (await client.get("/api/tasks")).json()

    assert [t["id"] for t in listing] == [second["id"], first["id"]]
    assert set(listing[0]) == {
        "id",
        "conversation_id",
        "origin",
        "status",
        "request_text",
        "final_answer",
        "error",
        "step_count",
        "created_at",
        "updated_at",
    }
    assert listing[0]["request_text"] == "두 번째"
    assert listing[0]["step_count"] == 0


async def test_message_rate_limit_returns_429(
    client: httpx.AsyncClient, agent: MyAgent, conversation: int, app
) -> None:
    app.state.message_rate_limiter = SlidingWindowRateLimiter(limit=2, window_seconds=60)

    with agent.override(model=scripted(text("네")).model()):
        statuses = [
            (await client.post(f"/api/conversations/{conversation}/messages", json=message_body(f"{i}"))).status_code
            for i in range(3)
        ]

    assert statuses == [202, 202, 429]


def test_rate_limiter_window_slides() -> None:
    now = [1000.0]
    limiter = SlidingWindowRateLimiter(limit=2, window_seconds=60, clock=lambda: now[0])

    assert limiter.allow(1) is True
    assert limiter.allow(1) is True
    assert limiter.allow(1) is False
    assert limiter.allow(2) is True  # 사용자마다 따로 센다
    now[0] += 61
    assert limiter.allow(1) is True


def test_default_rate_limit_is_20_per_minute() -> None:
    limiter = SlidingWindowRateLimiter()

    assert (limiter.limit, limiter.window_seconds) == (20, 60)
