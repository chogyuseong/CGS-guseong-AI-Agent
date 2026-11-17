"""4단계 보강(2): 일정 수정·삭제의 대상 버전과 정책 (BUILD_SPEC 3.5, 4.3, 7.3, 13절 4단계).

공식 문서(2026-11-17 확인, guides/version-resources): 수정·삭제에 `If-Match: <etag>`를 주면, 그 사이 바뀐 일정이면 412.
- 계획을 만들 때 일정의 etag를 target_version으로 고정한다.
- 실행 때 지금 일정을 읽어 etag가 다르면 보내지 않고 stale. 같아도 If-Match로 보내고, 412면 stale.
- stale이면 모델에게 "바뀌었으니 다시 확인해 새로 요청"을 알린다 → 새 계획·새 승인(재승인).
- 참석자가 있는 일정(나 말고 다른 사람이 있거나, 남이 만든 초대)과 반복 일정 시리즈 원본은 바꾸지 않는다.
- 시간 초과 뒤에는 지금 상태를 다시 읽어 판단한다
  (삭제: 없어졌으면 완료, 수정: 바뀐 칸이 요청과 같으면 완료, 아니면 불명).
"""

import json

import httpx
import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.agent import MyAgent
from app.agent.deps import AgentDeps
from app.agent.plans import PlanRef
from app.agent.results import ToolStatus
from app.models import ActionPlan, Operation
from app.models.enums import OperationStatus
from app.tools import gcal
from tests.conftest import tool_context
from tests.fake_http import FakeHttp, fixture_json, fixture_response, json_response
from tests.model_scripts import call, scripted, text
from tests.plan_helpers import approve_directly
from tests.test_agent_run import send
from tests.test_approvals import approve, task_detail
from tests.test_gcal import EVENTS, connect_google

MEETING = f"{EVENTS}/evt0meeting001"
ETAG = '"3500000000000000"'


def meeting(**changes: object) -> dict:
    return {**fixture_json("google_event_meeting.json"), **changes}


async def operation_of(db_session: AsyncSession, call_id: str) -> tuple[ActionPlan, Operation]:
    plan = await db_session.scalar(select(ActionPlan).where(ActionPlan.tool_call_id == call_id))
    assert plan is not None
    operation = await db_session.scalar(
        select(Operation).where(Operation.plan_id == plan.id).execution_options(populate_existing=True)
    )
    assert operation is not None
    return plan, operation


async def approved(
    db_session: AsyncSession, deps: AgentDeps, fake_http: FakeHttp, tool: str, call_id: str, args: dict
) -> None:
    await connect_google(deps)
    fake_http.route("GET", MEETING, json_response(200, meeting()))  # 계획 때 읽는 일정(etag 고정)
    await approve_directly(db_session, deps, tool, call_id, args)


UPDATE_ARGS = {"event_id": "evt0meeting001", "title": "주간 회의"}
DELETE_ARGS = {"event_id": "evt0meeting001"}


async def test_plan_records_target_etag(tool_deps: AgentDeps, fake_http: FakeHttp, db_session: AsyncSession) -> None:
    await approved(db_session, tool_deps, fake_http, "update_event", "call-u0", UPDATE_ARGS)

    plan, _ = await operation_of(db_session, "call-u0")
    assert plan.target_resource_id == "evt0meeting001"
    assert plan.target_version == ETAG


async def test_update_sends_if_match_with_planned_etag(
    tool_deps: AgentDeps, fake_http: FakeHttp, db_session: AsyncSession
) -> None:
    await approved(db_session, tool_deps, fake_http, "update_event", "call-u1", UPDATE_ARGS)
    fake_http.route("PATCH", MEETING, json_response(200, meeting(summary="주간 회의", etag='"3500000000000001"')))

    result = await gcal.update_event(tool_context(tool_deps, "update_event", "call-u1"), **UPDATE_ARGS)

    [patch] = fake_http.sent("PATCH", MEETING)
    _, operation = await operation_of(db_session, "call-u1")
    assert patch.headers["If-Match"] == ETAG
    assert json.loads(patch.content) == {"summary": "주간 회의"}
    assert result.status == ToolStatus.SUCCEEDED
    assert operation.status == OperationStatus.SUCCEEDED


async def test_changed_before_execution_is_stale_without_sending(
    tool_deps: AgentDeps, fake_http: FakeHttp, db_session: AsyncSession
) -> None:
    """승인한 뒤 다른 기기에서 일정을 바꿨다. 조용히 덮어쓰지 않는다(BUILD_SPEC 3.5)."""
    await approved(db_session, tool_deps, fake_http, "update_event", "call-u2", UPDATE_ARGS)
    fake_http.route("GET", MEETING, json_response(200, meeting(etag='"3500000000000009"', summary="바뀐 회의")))

    result = await gcal.update_event(tool_context(tool_deps, "update_event", "call-u2"), **UPDATE_ARGS)

    _, operation = await operation_of(db_session, "call-u2")
    assert fake_http.sent("PATCH", MEETING) == []
    assert result.status == ToolStatus.FAILED
    assert result.error_code == "target_changed"
    assert operation.status == OperationStatus.STALE


async def test_412_from_google_is_stale(tool_deps: AgentDeps, fake_http: FakeHttp, db_session: AsyncSession) -> None:
    await approved(db_session, tool_deps, fake_http, "delete_event", "call-d1", DELETE_ARGS)
    fake_http.route("DELETE", MEETING, fixture_response("google_error_412.json", status=412))

    result = await gcal.delete_event(tool_context(tool_deps, "delete_event", "call-d1"), **DELETE_ARGS)

    [request] = fake_http.sent("DELETE", MEETING)
    _, operation = await operation_of(db_session, "call-d1")
    assert request.headers["If-Match"] == ETAG
    assert result.error_code == "target_changed"
    assert operation.status == OperationStatus.STALE


async def test_delete_sends_if_match(tool_deps: AgentDeps, fake_http: FakeHttp, db_session: AsyncSession) -> None:
    await approved(db_session, tool_deps, fake_http, "delete_event", "call-d2", DELETE_ARGS)
    fake_http.route("DELETE", MEETING, httpx.Response(204))

    result = await gcal.delete_event(tool_context(tool_deps, "delete_event", "call-d2"), **DELETE_ARGS)

    [request] = fake_http.sent("DELETE", MEETING)
    assert request.headers["If-Match"] == ETAG
    assert result.status == ToolStatus.SUCCEEDED


ATTENDEES = [{"email": "owner@example.com", "self": True}, {"email": "friend@example.com"}]


@pytest.mark.parametrize(
    ("tool", "args", "changes", "code"),
    [
        ("update_event", UPDATE_ARGS, {"attendees": ATTENDEES}, "has_attendees"),
        ("delete_event", DELETE_ARGS, {"attendees": ATTENDEES}, "has_attendees"),
        ("update_event", UPDATE_ARGS, {"organizer": {"email": "boss@example.com"}}, "has_attendees"),
        ("delete_event", DELETE_ARGS, {"recurrence": ["RRULE:FREQ=WEEKLY"]}, "recurring_series"),
    ],
)
async def test_events_with_attendees_or_series_are_refused(
    tool_deps: AgentDeps,
    fake_http: FakeHttp,
    db_session: AsyncSession,
    tool: str,
    args: dict,
    changes: dict,
    code: str,
) -> None:
    await connect_google(tool_deps)
    fake_http.route("GET", MEETING, json_response(200, meeting(**changes)))
    await approve_directly(db_session, tool_deps, tool, f"call-{code}-{tool}", args)

    result = await getattr(gcal, tool)(tool_context(tool_deps, tool, f"call-{code}-{tool}"), **args)

    assert result.status == ToolStatus.FAILED
    assert result.error_code == code
    assert fake_http.sent("PATCH", MEETING) == fake_http.sent("DELETE", MEETING) == []


async def test_preview_warns_about_attendees(tool_deps: AgentDeps, fake_http: FakeHttp) -> None:
    await connect_google(tool_deps)
    fake_http.route("GET", MEETING, json_response(200, meeting(attendees=ATTENDEES)))

    text_ = await gcal.preview_delete_event(tool_deps, DELETE_ARGS)

    assert "참석자가 있는 일정이라 승인해도 삭제하지 않습니다" in text_


async def test_plan_without_version_is_not_executed(
    tool_deps: AgentDeps, fake_http: FakeHttp, db_session: AsyncSession
) -> None:
    """계획 때 일정을 읽지 못했으면(대상 버전 없음) 보지 못한 일정을 바꾸지 않는다."""
    await approved(db_session, tool_deps, fake_http, "delete_event", "call-d3", DELETE_ARGS)
    await db_session.execute(update(ActionPlan).where(ActionPlan.tool_call_id == "call-d3").values(target_version=None))
    await db_session.commit()

    result = await gcal.delete_event(tool_context(tool_deps, "delete_event", "call-d3"), **DELETE_ARGS)

    assert result.error_code == "target_unknown"
    assert fake_http.sent("DELETE", MEETING) == []


async def test_reconcile_delete_and_update(tool_deps: AgentDeps, fake_http: FakeHttp) -> None:
    import uuid

    await connect_google(tool_deps)
    ref = PlanRef(uuid.uuid4(), tool_deps.user_id, "delete_event", DELETE_ARGS, "evt0meeting001")
    fake_http.route("GET", MEETING, fixture_response("google_error_404.json", status=404))
    assert (await gcal.reconcile_delete(tool_deps, ref)).result == "done"

    full = {**UPDATE_ARGS, "start": None, "end": None, "location": None, "description": None}
    ref = PlanRef(uuid.uuid4(), tool_deps.user_id, "update_event", full, "evt0meeting001")
    fake_http.route("GET", MEETING, json_response(200, meeting(summary="주간 회의")))
    assert (await gcal.reconcile_update(tool_deps, ref)).result == "done"
    fake_http.route("GET", MEETING, json_response(200, meeting(summary="팀 회의")))
    assert (await gcal.reconcile_update(tool_deps, ref)).result == "unknown"


async def test_stale_update_asks_for_new_approval(
    client: httpx.AsyncClient, agent: MyAgent, conversation: int, fake_http: FakeHttp, tool_deps: AgentDeps
) -> None:
    """승인 대기 중 대상이 바뀌면: 실행하지 않고(stale) 모델이 다시 요청 → 새 계획·새 승인(재승인)."""
    await connect_google(tool_deps)
    fake_http.route("GET", MEETING, json_response(200, meeting()))
    model = scripted(
        call("update_event", **UPDATE_ARGS),
        call("update_event", **UPDATE_ARGS),
        text("수정했습니다."),
    )
    with agent.override(model=model.model()):
        task = await send(client, conversation, "팀 회의 제목을 주간 회의로 바꿔줘")
        # 승인을 기다리는 동안 다른 기기에서 일정이 바뀌었다.
        fake_http.route("GET", MEETING, json_response(200, meeting(etag='"3500000000000009"', summary="바뀐 회의")))
        await approve(client, task["approvals"][0]["id"])

    detail = await task_detail(client, task["id"])
    assert fake_http.sent("PATCH", MEETING) == []
    assert detail["status"] == "waiting_approval"
    assert [a["status"] for a in detail["approvals"]] == ["approved", "pending"]
    assert detail["approvals"][0]["operation_status"] == "stale"
    assert detail["steps"][0]["output"]["error_code"] == "target_changed"
