"""4단계 보강(1): 일정 생성 중복 방지 (BUILD_SPEC 4.3, 13절 4단계).

공식 문서(2026-11-17 확인, events/insert, guides/version-resources):
- 생성 때 event id를 직접 줄 수 있다. base32hex(소문자 a-v, 숫자 0-9), 5~1024자, 캘린더 안에서 유일.
- 같은 id가 이미 있으면 insert가 성공하지 않는다(insert에는 조건부 요청이 없다).

그래서 operation마다 고정된 event id(ma + operation_id 16진수)를 쓴다. 재시도·복구 때도 같은 id다.
- 성공 응답 → 영수증(외부 리소스 id = event id)
- 409(중복) → 그 id의 일정을 읽어 계획과 내용이 같을 때만 우리 결과로 연결. 다르면 실패(남의 일정을 성공으로 보지 않음)
- 시간 초과 → 결과 불명 → 같은 id로 다시 조회해 확인(있으면 성공, 없으면 실행 안 됨)
"""

import json
import uuid

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.deps import AgentDeps
from app.agent.plans import PlanRef
from app.agent.results import ToolStatus
from app.models import ActionPlan, ExecutionReceipt, Operation
from app.models.enums import OperationStatus
from app.tools import gcal
from tests.conftest import tool_context
from tests.fake_http import FakeHttp, fixture_json, fixture_response, json_response
from tests.plan_helpers import approve_directly
from tests.test_gcal import CREATE_ARGS, EVENTS, connect_google


async def plan_for(db_session: AsyncSession, call_id: str) -> ActionPlan:
    plan = await db_session.scalar(select(ActionPlan).where(ActionPlan.tool_call_id == call_id))
    assert plan is not None
    return plan


def event_like(event_id: str, **changes: object) -> dict:
    """CREATE_ARGS로 만든 일정과 같은 내용의 Events 리소스(서울 시각)."""
    event = {**fixture_json("google_event_created.json"), "id": event_id}
    return {**event, **changes}


async def approved_create(db_session: AsyncSession, deps: AgentDeps, call_id: str) -> str:
    await connect_google(deps)
    await approve_directly(db_session, deps, "create_event", call_id, CREATE_ARGS)
    return gcal.stable_event_id((await plan_for(db_session, call_id)).operation_id)


async def operation_and_receipt(db_session: AsyncSession, call_id: str) -> tuple[Operation, ExecutionReceipt | None]:
    plan = await plan_for(db_session, call_id)
    operation = await db_session.scalar(
        select(Operation).where(Operation.plan_id == plan.id).execution_options(populate_existing=True)
    )
    assert operation is not None
    receipt = await db_session.scalar(select(ExecutionReceipt).where(ExecutionReceipt.operation_id == operation.id))
    return operation, receipt


def test_stable_event_id_follows_calendar_rules() -> None:
    operation_id = uuid.UUID("12345678-9abc-def0-1234-56789abcdef0")
    event_id = gcal.stable_event_id(operation_id)

    assert event_id == "ma123456789abcdef0123456789abcdef0"
    assert set(event_id) <= set("abcdefghijklmnopqrstuv0123456789")
    assert 5 <= len(event_id) <= 1024
    assert gcal.stable_event_id(operation_id) == event_id  # 재시도·복구에도 같은 id


async def test_create_sends_stable_id_and_records_it(
    tool_deps: AgentDeps, fake_http: FakeHttp, db_session: AsyncSession
) -> None:
    event_id = await approved_create(db_session, tool_deps, "call-c1")
    fake_http.route("POST", EVENTS, json_response(200, event_like(event_id)))

    result = await gcal.create_event(tool_context(tool_deps, "create_event", "call-c1"), **CREATE_ARGS)

    [request] = fake_http.sent("POST", EVENTS)
    operation, receipt = await operation_and_receipt(db_session, "call-c1")
    assert json.loads(request.content)["id"] == event_id
    assert result.status == ToolStatus.SUCCEEDED
    assert operation.status == OperationStatus.SUCCEEDED
    assert receipt is not None and receipt.external_resource_id == event_id


async def test_duplicate_id_with_same_content_is_linked_as_ours(
    tool_deps: AgentDeps, fake_http: FakeHttp, db_session: AsyncSession
) -> None:
    """앞선 시도가 실제로 만들었다(응답만 잃음). 같은 id·같은 내용이면 그 일정을 결과로 연결한다."""
    event_id = await approved_create(db_session, tool_deps, "call-c2")
    fake_http.route("POST", EVENTS, fixture_response("google_error_409_duplicate.json", status=409))
    fake_http.route("GET", f"{EVENTS}/{event_id}", json_response(200, event_like(event_id)))

    result = await gcal.create_event(tool_context(tool_deps, "create_event", "call-c2"), **CREATE_ARGS)

    operation, receipt = await operation_and_receipt(db_session, "call-c2")
    assert result.status == ToolStatus.SUCCEEDED
    assert len(fake_http.sent("POST", EVENTS)) == 1
    assert operation.status == OperationStatus.SUCCEEDED
    assert receipt is not None and receipt.external_resource_id == event_id


async def test_duplicate_id_with_other_content_is_not_success(
    tool_deps: AgentDeps, fake_http: FakeHttp, db_session: AsyncSession
) -> None:
    event_id = await approved_create(db_session, tool_deps, "call-c3")
    fake_http.route("POST", EVENTS, fixture_response("google_error_409_duplicate.json", status=409))
    fake_http.route("GET", f"{EVENTS}/{event_id}", json_response(200, event_like(event_id, summary="다른 일정")))

    result = await gcal.create_event(tool_context(tool_deps, "create_event", "call-c3"), **CREATE_ARGS)

    operation, receipt = await operation_and_receipt(db_session, "call-c3")
    assert result.status == ToolStatus.FAILED
    assert result.error_code == "id_conflict"
    assert operation.status == OperationStatus.FAILED
    assert receipt is None


async def test_timeout_then_found_is_success(
    tool_deps: AgentDeps, fake_http: FakeHttp, db_session: AsyncSession
) -> None:
    """응답 전에 끊겼지만 Google은 만들었다. 같은 id로 조회해 확인하고, 다시 만들지 않는다."""
    event_id = await approved_create(db_session, tool_deps, "call-c4")
    fake_http.route("POST", EVENTS, httpx.ReadTimeout("느림"))
    fake_http.route("GET", f"{EVENTS}/{event_id}", json_response(200, event_like(event_id)))

    result = await gcal.create_event(tool_context(tool_deps, "create_event", "call-c4"), **CREATE_ARGS)

    operation, receipt = await operation_and_receipt(db_session, "call-c4")
    assert result.status == ToolStatus.SUCCEEDED
    assert len(fake_http.sent("POST", EVENTS)) == 1
    assert operation.status == OperationStatus.SUCCEEDED
    assert receipt is not None and receipt.external_resource_id == event_id


async def test_timeout_then_missing_is_not_executed(
    tool_deps: AgentDeps, fake_http: FakeHttp, db_session: AsyncSession
) -> None:
    event_id = await approved_create(db_session, tool_deps, "call-c5")
    fake_http.route("POST", EVENTS, httpx.ReadTimeout("느림"))
    fake_http.route("GET", f"{EVENTS}/{event_id}", fixture_response("google_error_404.json", status=404))

    result = await gcal.create_event(tool_context(tool_deps, "create_event", "call-c5"), **CREATE_ARGS)

    operation, _ = await operation_and_receipt(db_session, "call-c5")
    assert result.status == ToolStatus.FAILED
    assert result.error_code == "not_executed"
    assert operation.status == OperationStatus.FAILED
    assert len(fake_http.sent("POST", EVENTS)) == 1


async def test_timeout_and_lookup_failure_stays_unknown(
    tool_deps: AgentDeps, fake_http: FakeHttp, db_session: AsyncSession
) -> None:
    event_id = await approved_create(db_session, tool_deps, "call-c6")
    fake_http.route("POST", EVENTS, httpx.ReadTimeout("느림"))
    fake_http.route("GET", f"{EVENTS}/{event_id}", httpx.ConnectError("끊김"))

    result = await gcal.create_event(tool_context(tool_deps, "create_event", "call-c6"), **CREATE_ARGS)

    operation, receipt = await operation_and_receipt(db_session, "call-c6")
    assert result.status == ToolStatus.UNKNOWN
    assert operation.status == OperationStatus.UNKNOWN
    assert receipt is None


async def test_reconcile_after_crash_uses_stable_id(tool_deps: AgentDeps, fake_http: FakeHttp) -> None:
    """복구 때 executor가 부르는 재조회 함수: 같은 id로 조회해 결과를 판단한다."""
    await connect_google(tool_deps)
    operation_id = uuid.uuid4()
    event_id = gcal.stable_event_id(operation_id)
    ref = PlanRef(operation_id, tool_deps.user_id, "create_event", {**CREATE_ARGS, "description": None}, None)
    fake_http.route("GET", f"{EVENTS}/{event_id}", json_response(200, event_like(event_id)))

    found = await gcal.reconcile_create(tool_deps, ref)

    assert found.result == "done"
    assert found.resource_id == event_id
    assert gcal.SPECS["create_event"].reconcile is gcal.reconcile_create
