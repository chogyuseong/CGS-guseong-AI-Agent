"""2단계 보강(4): 승인 카드에 필요한 값 (BUILD_SPEC 3.6 표시, 10.1, 절대 원칙 10).

카드는 날짜·대상·계정·변경 전후를 감시 배지보다 눈에 띄게 보여 주고, 실행 결과는 영수증으로만 "완료"라 한다.
- target: 어디에 하는 일인지(도구 규격의 target_label)
- account: 연결이 필요한 도구면 계획 때 연결 상태, 아니면 null
- execute_before: 승인 뒤 실행 마감(승인 전 null)
- operation_status, receipt_confirmed: 실행 상태와 영수증 여부
"""

import httpx
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.agent import MyAgent
from app.agent.plans import ExternalToolSpec
from app.agent.toolsets import EXTERNAL_TOOL_SPECS
from app.models import Approval
from tests.fake_tools import FakeExternalCounter
from tests.test_agent_run import send
from tests.test_approvals import approve, external_then_answer, task_detail


async def test_pending_card_shows_target_and_no_execution_yet(
    client: httpx.AsyncClient, agent: MyAgent, conversation: int, fake_external_counter: FakeExternalCounter
) -> None:
    with agent.override(model=external_then_answer().model()):
        task = await send(client, conversation, "외부 도구 실행해줘")

    [card] = task["approvals"]
    assert card["target"] == "가짜 외부 서비스"
    assert card["account"] is None
    assert card["execute_before"] is None
    assert card["operation_status"] == "awaiting_approval"
    assert card["receipt_confirmed"] is False
    assert card["tool_input"] == {"value": 7}


async def test_card_after_execution_is_confirmed_by_receipt(
    client: httpx.AsyncClient, agent: MyAgent, conversation: int, fake_external_counter: FakeExternalCounter
) -> None:
    with agent.override(model=external_then_answer().model()):
        task = await send(client, conversation, "외부 도구 실행해줘")
        await approve(client, task["approvals"][0]["id"])

    [card] = (await task_detail(client, task["id"]))["approvals"]
    assert card["status"] == "approved"
    assert card["execute_before"] is not None
    assert card["operation_status"] == "succeeded"
    assert card["receipt_confirmed"] is True


async def test_card_account_reflects_connection_at_plan_time(
    client: httpx.AsyncClient,
    agent: MyAgent,
    conversation: int,
    fake_external_counter: FakeExternalCounter,
    monkeypatch,
) -> None:
    spec = ExternalToolSpec(version="1", provider="google", target_label="Google 캘린더(기본 캘린더)")
    monkeypatch.setitem(EXTERNAL_TOOL_SPECS, "fake_external", spec)
    with agent.override(model=external_then_answer().model()):
        task = await send(client, conversation, "외부 도구 실행해줘")

    [card] = task["approvals"]
    assert card["target"] == "Google 캘린더(기본 캘린더)"
    assert card["account"] == "Google 계정이 연결되어 있지 않음"


async def test_legacy_card_without_plan_still_renders(
    client: httpx.AsyncClient,
    agent: MyAgent,
    conversation: int,
    db_session: AsyncSession,
    fake_external_counter: FakeExternalCounter,
) -> None:
    """2단계 보강 전 승인(plan 없음)도 카드가 깨지지 않는다."""
    with agent.override(model=external_then_answer().model()):
        task = await send(client, conversation, "외부 도구 실행해줘")
    await db_session.execute(update(Approval).where(Approval.id == task["approvals"][0]["id"]).values(plan_id=None))
    await db_session.commit()

    [card] = (await task_detail(client, task["id"]))["approvals"]
    assert card["target"] == "외부 서비스"
    assert card["operation_status"] is None
    assert card["receipt_confirmed"] is False
