"""3단계 보강(4): 푸시 notify와 notifications 기록(BUILD_SPEC 8.4).

notify(user_id, kind, internal_url, notification_key)

- 같은 notification_key는 한 번만 보낸다(복구·재시도로 같은 알림이 두 번 가지 않게).
  정확히 한 번 전달을 주장하지는 않는다.
- URL은 앱 내부 경로만. 바깥 주소면 보내지 않는다.
- notifications에는 종류·경로·결과·보낸 기기 수만 남긴다(본문·사용자 글 없음).
- 발송 실패는 작업을 실패시키지 않고 failed로 남긴다. 404·410 구독 정리는 tests/test_push.py.
"""

import json

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.agent import MyAgent
from app.agent.workflows import services_from_state
from app.config import Settings
from app.models import Notification, PushSubscription, User
from app.models.enums import NotificationStatus
from app.push import PUSH_BODIES, PushKind, build_payload
from tests.fake_push import FakePushSender
from tests.fake_tools import FakeExternalCounter
from tests.model_scripts import call, scripted, text
from tests.test_agent_run import send
from tests.test_push import AUTH, ENDPOINT, P256DH, SECRET


async def subscribed(db_session: AsyncSession, user: User) -> None:
    db_session.add(PushSubscription(user_id=user.id, endpoint=ENDPOINT, p256dh=P256DH, auth=AUTH))
    await db_session.commit()


async def notifications_of(db_session: AsyncSession, user_id: int) -> list[Notification]:
    rows = await db_session.scalars(
        select(Notification).where(Notification.user_id == user_id).execution_options(populate_existing=True)
    )
    return list(rows)


async def test_same_key_is_sent_once(
    app: FastAPI, push_on: Settings, push_sender: FakePushSender, logged_in_user: User, db_session: AsyncSession
) -> None:
    user_id = logged_in_user.id
    await subscribed(db_session, logged_in_user)
    notifier = services_from_state(app.state).notifier

    first = await notifier.notify(user_id, PushKind.TASK_DONE, "/chat/3", "task_done:task-9:task-9")
    second = await notifier.notify(user_id, PushKind.TASK_DONE, "/chat/3", "task_done:task-9:task-9")

    [row] = await notifications_of(db_session, user_id)
    assert (first, second) == (1, 0)
    assert len(push_sender.sent) == 1
    assert row.status == NotificationStatus.SENT
    assert row.sent_count == 1
    assert (row.kind, row.url) == (PushKind.TASK_DONE, "/chat/3")


@pytest.mark.parametrize("url", ["https://evil.example/x", "//evil.example/x", "chat/3", "/\\evil.example"])
async def test_outside_url_is_not_sent(
    app: FastAPI,
    push_on: Settings,
    push_sender: FakePushSender,
    logged_in_user: User,
    db_session: AsyncSession,
    url: str,
) -> None:
    user_id = logged_in_user.id
    await subscribed(db_session, logged_in_user)

    sent = await services_from_state(app.state).notifier.notify(user_id, PushKind.TASK_DONE, url, "k-1")

    assert sent == 0
    assert push_sender.sent == []
    assert await notifications_of(db_session, user_id) == []


async def test_failed_delivery_is_recorded_and_task_still_waits(
    client: httpx.AsyncClient,
    agent: MyAgent,
    conversation: int,
    push_on: Settings,
    push_sender: FakePushSender,
    logged_in_user: User,
    db_session: AsyncSession,
    fake_external_counter: FakeExternalCounter,
) -> None:
    user_id = logged_in_user.id
    await subscribed(db_session, logged_in_user)
    push_sender.failures[ENDPOINT] = RuntimeError("푸시 서버 응답 500")

    with agent.override(model=scripted(call("fake_external", value=1), text("끝")).model()):
        task = await send(client, conversation, SECRET)

    [row] = await notifications_of(db_session, user_id)
    assert task["status"] == "waiting_approval"
    assert row.kind == PushKind.APPROVAL_NEEDED
    assert row.status == NotificationStatus.FAILED
    assert row.notification_key == f"approval_needed:task-{task['id']}:task-{task['id']}"
    assert SECRET not in json.dumps([row.kind, row.url, row.notification_key, row.status])


async def test_disabled_push_is_recorded_without_sending(
    app: FastAPI, push_sender: FakePushSender, logged_in_user: User, db_session: AsyncSession
) -> None:
    user_id = logged_in_user.id
    await subscribed(db_session, logged_in_user)

    sent = await services_from_state(app.state).notifier.notify(user_id, PushKind.TASK_FAILED, "/chat/1", "k-2")

    [row] = await notifications_of(db_session, user_id)
    assert sent == 0
    assert row.status == NotificationStatus.DISABLED
    assert push_sender.sent == []


def test_needs_review_body_is_fixed_text() -> None:
    payload = json.loads(build_payload(PushKind.TASK_NEEDS_REVIEW, "/chat/1"))
    assert payload["body"] == PUSH_BODIES[PushKind.TASK_NEEDS_REVIEW] == "처리 결과 확인이 필요합니다"
    assert PUSH_BODIES[PushKind.BRIEFING] == "브리핑이 준비되었습니다"
