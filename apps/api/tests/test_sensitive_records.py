"""3단계 보강(5): 로그·기록 민감 정보 검사 (BUILD_SPEC 13절 3단계, 5.3, 절대 원칙 11).

장애·재시도 경로는 오류를 로그에 남기기 쉬운 곳이다. 사용자 메시지와 외부 콘텐츠(일정 제목)에 비밀 문장을 넣고
재조회 실패, outbox 시작 실패, 푸시 실패, 외부 쓰기 시간 초과를 거친 뒤 다음에 그 문장이 없는지 본다.
- 앱 로그(app.* 로거, 예외 traceback 포함)
- 3단계 보강에서 새로 만든 기록: outbox_events, notifications, operations.error_code
범위 밖(그대로 남는 곳, docs/FAULTS.md·STATUS.md에 적음): messages·tasks.message_history·steps(화면에 보여 줄 내용),
execution_receipts.summary(도구 결과), DBOS 시스템 테이블의 워크플로 입력·단계 결과(5·7단계에서 다룬다).
"""

import json
import logging

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.agent import MyAgent
from app.config import Settings
from app.models import Notification, Operation, OutboxEvent, PushSubscription, User
from app.observability import install_log_redaction
from app.tools import gcal
from tests.conftest import tool_context
from tests.fake_http import FakeHttp
from tests.fake_push import FakePushSender
from tests.fake_service import FakeExternalService
from tests.fake_tools import FakeExternalCounter
from tests.plan_helpers import approve_directly
from tests.test_faults import approved_plan, crash_during_execution, recover
from tests.test_gcal import CREATE_ARGS, EVENTS, connect_google
from tests.test_outbox import BrokenLauncher, post_message
from tests.test_push import AUTH, ENDPOINT, P256DH

SECRET = "비밀메모-7731 엄마 생일 선물 위치"


@pytest.fixture
def app_logs(caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    caplog.set_level(logging.DEBUG, logger="app")
    return caplog


def assert_no_secret(caplog: pytest.LogCaptureFixture) -> None:
    text = caplog.text + "\n".join(f"{record.getMessage()} {record.exc_text or ''}" for record in caplog.records)
    assert SECRET not in text
    assert "7731" not in text


async def test_failure_paths_do_not_log_user_text(
    app: FastAPI,
    client: httpx.AsyncClient,
    agent: MyAgent,
    conversation: int,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    logged_in_user: User,
    push_on: Settings,
    push_sender: FakePushSender,
    fake_service: FakeExternalService,
    fake_external_counter: FakeExternalCounter,
    app_logs: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = logged_in_user.id
    db_session.add(PushSubscription(user_id=user_id, endpoint=ENDPOINT, p256dh=P256DH, auth=AUTH))
    await db_session.commit()
    push_sender.failures[ENDPOINT] = RuntimeError(f"push 500 {SECRET}")  # 푸시 서버 오류 문장에도 섞여 온다고 가정

    # outbox 시작 실패(오류 문장에 비밀이 있어도 종류 이름만 남긴다)
    working = app.state.launcher
    app.state.launcher = BrokenLauncher()
    await post_message(client, conversation, SECRET)
    app.state.launcher = working

    # 승인 대기(푸시 실패) → 실행 도중 끊김 → 재조회 실패(unknown)
    task, plan = await approved_plan(client, agent, conversation, db_session, user_id)
    await crash_during_execution(db_session, plan)

    def lookup_down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"조회 불가 {SECRET}", request=request)

    monkeypatch.setattr(fake_service, "handle", lookup_down)
    await recover(agent, session_factory, task["id"])

    assert_no_secret(app_logs)
    stored = [
        *(await db_session.scalars(select(OutboxEvent))),
        *(await db_session.scalars(select(Notification))),
        *(await db_session.scalars(select(Operation))),
    ]
    dumped = json.dumps(
        [{c.name: str(getattr(row, c.name)) for c in row.__table__.columns} for row in stored], ensure_ascii=False
    )
    assert SECRET not in dumped and "7731" not in dumped


async def test_calendar_write_timeout_does_not_log_title(
    tool_deps, fake_http: FakeHttp, db_session: AsyncSession, app_logs: pytest.LogCaptureFixture
) -> None:
    """외부 쓰기가 시간 초과로 결과 불명이 돼도 일정 제목(사용자 글·외부 콘텐츠)을 로그에 남기지 않는다."""
    await connect_google(tool_deps)
    args = {**CREATE_ARGS, "title": SECRET}
    await approve_directly(db_session, tool_deps, "create_event", "call-secret", args)
    fake_http.route("POST", EVENTS, httpx.ReadTimeout(f"느림 {SECRET}"))

    result = await gcal.create_event(tool_context(tool_deps, "create_event", "call-secret"), **args)

    assert result.status == "unknown"
    assert_no_secret(app_logs)
    operation = await db_session.scalar(select(Operation).execution_options(populate_existing=True))
    assert operation is not None and SECRET not in (operation.error_code or "")


def test_app_log_keeps_exception_type_but_not_message(app_logs: pytest.LogCaptureFixture) -> None:
    install_log_redaction()
    logger = logging.getLogger("app.agent.example")
    try:
        raise ValueError(SECRET)
    except ValueError:
        logger.warning("실패", exc_info=True)
        logger.exception("또 실패")

    assert_no_secret(app_logs)
    assert "ValueError" in app_logs.text
    assert "test_sensitive_records.py" in app_logs.text  # 코드 위치는 남는다
