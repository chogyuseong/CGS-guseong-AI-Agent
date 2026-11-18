"""'내 확인이 필요한 작업' 모아보기 API 테스트(GET /api/tasks?attention=true).

요구사항 밖 기능으로 2026-11-18 사용자가 넣기로 정했다(docs/DECISIONS.md 같은 날짜 "확인 필요 모아보기").
확인 필요 = 승인 대기(waiting_approval) + 결과 확인 필요(needs_review, 마지막 변경 7일 이내).
"""

from datetime import timedelta

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app import clock
from app.models import Conversation, Task, User
from app.models.enums import TaskOrigin, TaskStatus
from tests.conftest import UserFactory


async def add_task(
    db_session: AsyncSession, user: User, status: TaskStatus, *, age_days: int = 0, conversation_id: int | None = None
) -> Task:
    """상태와 마지막 변경 시각을 정한 task 하나를 바로 저장한다(모델 실행 없이 목록 필터만 보려고)."""
    if conversation_id is None:
        room = Conversation(user_id=user.id, title="확인 필요 테스트")
        db_session.add(room)
        await db_session.flush()
        conversation_id = room.id
    updated = clock.now_utc() - timedelta(days=age_days)
    task = Task(
        conversation_id=conversation_id,
        user_id=user.id,
        origin=TaskOrigin.CHAT,
        status=status,
        created_at=updated,
        updated_at=updated,
    )
    db_session.add(task)
    await db_session.commit()
    return task


async def test_attention_lists_only_waiting_approval_and_needs_review(
    client: httpx.AsyncClient, db_session: AsyncSession, logged_in_user: User
) -> None:
    waiting = await add_task(db_session, logged_in_user, TaskStatus.WAITING_APPROVAL)
    review = await add_task(db_session, logged_in_user, TaskStatus.NEEDS_REVIEW)
    for other in (TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.RUNNING, TaskStatus.CANCELLED):
        await add_task(db_session, logged_in_user, other)

    listing = (await client.get("/api/tasks", params={"attention": "true"})).json()

    assert {t["id"] for t in listing} == {waiting.id, review.id}


async def test_old_needs_review_drops_out_but_old_approval_stays(
    client: httpx.AsyncClient, db_session: AsyncSession, logged_in_user: User
) -> None:
    old_review = await add_task(db_session, logged_in_user, TaskStatus.NEEDS_REVIEW, age_days=8)
    old_waiting = await add_task(db_session, logged_in_user, TaskStatus.WAITING_APPROVAL, age_days=8)

    listing = (await client.get("/api/tasks", params={"attention": "true"})).json()

    assert [t["id"] for t in listing] == [old_waiting.id]
    assert old_review.id not in {t["id"] for t in listing}


async def test_attention_hides_other_users_tasks(
    client: httpx.AsyncClient, db_session: AsyncSession, make_user: UserFactory, logged_in_user: User
) -> None:
    other = await make_user("other@example.com", "password-other")
    await add_task(db_session, other, TaskStatus.WAITING_APPROVAL)

    listing = (await client.get("/api/tasks", params={"attention": "true"})).json()

    assert listing == []


async def test_attention_is_not_cut_by_newer_finished_tasks(
    client: httpx.AsyncClient, db_session: AsyncSession, logged_in_user: User
) -> None:
    waiting = await add_task(db_session, logged_in_user, TaskStatus.WAITING_APPROVAL, age_days=1)
    for _ in range(3):
        await add_task(db_session, logged_in_user, TaskStatus.DONE)

    listing = (await client.get("/api/tasks", params={"attention": "true", "limit": 2})).json()
    plain = (await client.get("/api/tasks", params={"limit": 2})).json()

    assert [t["id"] for t in listing] == [waiting.id]
    assert waiting.id not in {t["id"] for t in plain}
