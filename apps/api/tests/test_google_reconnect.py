"""7단계 보강: Google 재연결·권한 철회 처리(BUILD_SPEC 10.5 "인증 만료와 재연결 상태를 구분한다").

가짜 HTTP로 Google 응답을 흉내 낸다(공식 문서 형식, 실제 응답 아님).

1. 토큰 갱신이 invalid_grant(만료·철회)면 연결 행을 지우지 않고 "다시 연결 필요"로 표시하고 토큰은 지운다.
   - 연결 목록은 connected=false, reconnect_required=true(처음부터 연결 안 된 것과 구분).
   - 다음 호출은 Google에 요청하지 않고 같은 안내를 준다.
   - 그 연결로 만든 대기 중 실행 계획은 무효화한다(connection_revoked).
   - 다시 연결(콜백)하면 표시가 풀리고, 연결 해제도 된다.
2. Gmail이 범위 부족(403 insufficientPermissions)이면 메일 권한을 연결 기록에서 빼고 다시 추가하라고 안내한다.
   다른 403(API 사용 설정 등)은 권한을 빼지 않는다.
"""

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.deps import AgentDeps
from app.google_auth import CALENDAR_SCOPE, GMAIL_SCOPE, MAIL_SCOPE_MESSAGE, RECONNECT_MESSAGE
from app.models import ActionPlan, Connection, Operation
from app.models.enums import OperationStatus
from app.tools import gcal, gmail
from tests.conftest import tool_context
from tests.fake_http import FakeHttp, fixture_response, json_response
from tests.plan_helpers import approve_directly
from tests.test_gcal import CREATE_ARGS, EVENTS, connect_google, list_events
from tests.test_gmail import MESSAGES, connect_mail

TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"


async def row_of(db_session: AsyncSession, deps: AgentDeps) -> Connection | None:
    return await db_session.scalar(
        select(Connection).where(Connection.user_id == deps.user_id).execution_options(populate_existing=True)
    )


def revoked_refresh(fake_http: FakeHttp) -> None:
    fake_http.route("POST", TOKEN_URL, fixture_response("google_token_invalid_grant.json", status=400))
    fake_http.route("GET", EVENTS, fixture_response("google_error_401.json", status=401))


async def test_invalid_grant_marks_reconnect_required(
    client: httpx.AsyncClient, tool_deps: AgentDeps, fake_http: FakeHttp, db_session: AsyncSession
) -> None:
    await connect_google(tool_deps)
    revoked_refresh(fake_http)

    result = await list_events(tool_deps)

    assert (result.ok, result.error) == (False, RECONNECT_MESSAGE)
    row = await row_of(db_session, tool_deps)
    assert row is not None and row.reconnect_required_at is not None
    assert row.encrypted_token == ""  # 쓸 수 없는 토큰은 남기지 않는다
    listed = (await client.get("/api/connections")).json()[0]
    assert (listed["connected"], listed["reconnect_required"], listed["calendar"]) == (False, True, False)


async def test_after_revocation_tools_do_not_call_google(tool_deps: AgentDeps, fake_http: FakeHttp) -> None:
    await connect_google(tool_deps)
    revoked_refresh(fake_http)
    await list_events(tool_deps)
    sent_before = len(fake_http.requests)

    again = await list_events(tool_deps)

    assert again.error == RECONNECT_MESSAGE
    assert len(fake_http.requests) == sent_before


async def test_revocation_invalidates_pending_plans(
    tool_deps: AgentDeps, fake_http: FakeHttp, db_session: AsyncSession
) -> None:
    await connect_google(tool_deps)
    await approve_directly(db_session, tool_deps, "create_event", "call-revoked", CREATE_ARGS)
    revoked_refresh(fake_http)
    await list_events(tool_deps)

    operation = await db_session.scalar(
        select(Operation)
        .join(ActionPlan, ActionPlan.id == Operation.plan_id)
        .where(ActionPlan.tool_call_id == "call-revoked")
        .execution_options(populate_existing=True)
    )
    result = await gcal.create_event(tool_context(tool_deps, "create_event", "call-revoked"), **CREATE_ARGS)

    assert operation is not None
    assert (operation.status, operation.error_code) == (OperationStatus.CANCELLED, "connection_revoked")
    assert (result.ok, result.error_code) == (False, "connection_revoked")
    assert "다시 연결" in (result.error or "")
    assert fake_http.sent("POST", EVENTS) == []


async def test_reconnect_clears_the_flag(
    client: httpx.AsyncClient, tool_deps: AgentDeps, fake_http: FakeHttp, db_session: AsyncSession
) -> None:
    await connect_google(tool_deps)
    revoked_refresh(fake_http)
    await list_events(tool_deps)
    fake_http.route("POST", TOKEN_URL, fixture_response("google_token_exchange.json"))
    started = await client.post("/api/connections/google/start", json={})
    state = httpx.URL(started.json()["authorization_url"]).params["state"]

    await client.get("/api/connections/google/callback", params={"code": "c", "state": state})

    row = await row_of(db_session, tool_deps)
    assert row is not None and row.reconnect_required_at is None and row.encrypted_token
    listed = (await client.get("/api/connections")).json()[0]
    assert (listed["connected"], listed["reconnect_required"], listed["calendar"]) == (True, False, True)


async def test_disconnect_works_while_reconnect_is_required(
    client: httpx.AsyncClient, tool_deps: AgentDeps, fake_http: FakeHttp, db_session: AsyncSession
) -> None:
    await connect_google(tool_deps)
    revoked_refresh(fake_http)
    await list_events(tool_deps)

    response = await client.delete("/api/connections/google")

    assert response.status_code == 204
    assert await row_of(db_session, tool_deps) is None
    assert fake_http.sent("POST", REVOKE_URL) == []  # 지울 토큰이 없어 철회 요청도 없다


async def test_gmail_scope_loss_drops_mail_permission(
    client: httpx.AsyncClient, tool_deps: AgentDeps, fake_http: FakeHttp, db_session: AsyncSession
) -> None:
    await connect_mail(tool_deps)
    fake_http.route("GET", MESSAGES, fixture_response("gmail_error_403_scope.json", status=403))

    result = await gmail.list_mail(tool_context(tool_deps, "list_mail"))
    sent = len(fake_http.requests)
    again = await gmail.list_mail(tool_context(tool_deps, "list_mail"))

    assert (result.ok, result.error_code) == (False, "mail_scope_missing")
    assert result.error == gmail.MAIL_SCOPE_LOST_MESSAGE
    row = await row_of(db_session, tool_deps)
    assert row is not None and row.scopes == [CALENDAR_SCOPE]
    assert again.error == MAIL_SCOPE_MESSAGE and len(fake_http.requests) == sent  # 다시 묻지 않는다
    assert (await client.get("/api/connections")).json()[0]["mail"] is False


async def test_other_gmail_403_keeps_mail_permission(
    tool_deps: AgentDeps, fake_http: FakeHttp, db_session: AsyncSession
) -> None:
    await connect_mail(tool_deps)
    body = {
        "error": {"code": 403, "message": "Gmail API has not been used", "errors": [{"reason": "accessNotConfigured"}]}
    }
    fake_http.route("GET", MESSAGES, json_response(403, body))

    result = await gmail.list_mail(tool_context(tool_deps, "list_mail"))

    assert result.error == gmail.FORBIDDEN_MESSAGE
    row = await row_of(db_session, tool_deps)
    assert row is not None and GMAIL_SCOPE in row.scopes
