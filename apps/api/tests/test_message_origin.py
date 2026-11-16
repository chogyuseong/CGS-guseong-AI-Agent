"""8단계에 함께 고친 것(6단계 화면 작업자가 보고).

1. '예약 작업' 대화방은 제목으로 찾으므로, 사용자가 같은 제목의 대화를 API로 만들 수 없어야 한다
   (만들 수 있으면 자동 실행 결과가 사용자가 만든 대화로 들어갈 수 있다).
2. GET /api/conversations/{id}/messages의 각 메시지에 출처(origin: chat|schedule|watch)와
   감시 알림의 출처 링크(sources)를 더한다. 기존 필드는 그대로다.
"""

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.results import ToolResult
from app.automation import check, rules
from app.automation.watch import WatchReview
from app.models import Conversation, Message, Task, User, Watch
from app.models.enums import MessageRole, TaskOrigin, TaskStatus


@pytest.mark.parametrize("title", ["예약 작업", "  예약 작업  "])
async def test_user_cannot_create_conversation_with_automation_title(
    client: httpx.AsyncClient, logged_in_user: User, title: str
) -> None:
    response = await client.post("/api/conversations", json={"title": title})

    assert response.status_code == 422
    listing = await client.get("/api/conversations")
    assert listing.json() == []


async def test_similar_title_is_still_allowed(client: httpx.AsyncClient, logged_in_user: User) -> None:
    response = await client.post("/api/conversations", json={"title": "예약 작업 메모"})

    assert response.status_code == 201
    assert response.json()["title"] == "예약 작업 메모"


async def add_task_with_answer(
    db_session: AsyncSession, conversation: Conversation, origin: TaskOrigin, answer: str
) -> None:
    task = Task(conversation_id=conversation.id, user_id=conversation.user_id, origin=origin, status=TaskStatus.DONE)
    db_session.add(task)
    await db_session.flush()
    if origin == TaskOrigin.CHAT:
        db_session.add(Message(conversation_id=conversation.id, role=MessageRole.USER, content="질문", task_id=task.id))
    db_session.add(
        Message(conversation_id=conversation.id, role=MessageRole.ASSISTANT, content=answer, task_id=task.id)
    )
    await db_session.commit()


def met_review() -> WatchReview:
    return WatchReview(met=True, summary="비 올 확률이 70%입니다.", judged=True, model="watch-scripted", usage=None)


async def add_watch_alert(db_session: AsyncSession, user: User) -> None:
    now = datetime.now(UTC)
    watch = Watch(
        user_id=user.id,
        description="비 감시",
        tool_name="get_weather",
        tool_input={"place": "서울", "date": now.date().isoformat()},
        condition_text="비 올 확률 60% 넘음",
        interval_minutes=60,
        next_run_at=now,
        expires_at=now + timedelta(days=30),
    )
    db_session.add(watch)
    await db_session.flush()
    result = ToolResult.external({"rain": 70}, sources=["https://open-meteo.com/", "https://map.kakao.com/x"])
    await check.notify_met(db_session, watch, met_review(), result, now)
    await db_session.commit()


async def test_messages_have_origin_and_watch_sources(
    client: httpx.AsyncClient, db_session: AsyncSession, logged_in_user: User
) -> None:
    room = Conversation(user_id=logged_in_user.id, title=rules.AUTOMATION_CONVERSATION_TITLE)
    db_session.add(room)
    await db_session.flush()
    await add_task_with_answer(db_session, room, TaskOrigin.CHAT, "채팅 답변")
    await add_task_with_answer(db_session, room, TaskOrigin.SCHEDULE, "브리핑")
    await add_watch_alert(db_session, logged_in_user)

    response = await client.get(f"/api/conversations/{room.id}/messages")

    assert response.status_code == 200
    rows = [(m["role"], m["origin"], m["sources"]) for m in response.json()]
    assert rows == [
        ("user", "chat", []),
        ("assistant", "chat", []),
        ("assistant", "schedule", []),
        ("assistant", "watch", ["https://open-meteo.com/", "https://map.kakao.com/x"]),
    ]
    # 기존 필드는 그대로 있다(화면이 지금 쓰는 모양).
    first = response.json()[0]
    assert set(first) >= {"id", "role", "content", "task_id", "created_at"}
