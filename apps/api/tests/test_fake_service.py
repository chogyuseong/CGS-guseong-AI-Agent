"""2단계 보강(3): 가짜 외부 서비스와 승인 경로의 외부 효과 (BUILD_SPEC 13절 2단계 완료 증거).

- 가짜 서비스: 같은 멱등성 키는 효과 1건, 같은 키에 다른 본문은 409, 결과 조회, 서비스를 새로 만들어도 기록 유지
- 승인 전 외부 호출 0회 / 중복 승인(순차·동시) 후 외부 효과 1건 / 효과의 키 = 계획의 operation_id
- 재개 등록 전 서버 종료: 승인 결정은 커밋됐지만 재개 워크플로를 시작하기 전에 서버가 죽은 경우,
  서버 시작 때 restart_orphaned_tasks가 이어서 실행하고 효과는 1건이다(다시 해도 1건)
"""

import asyncio
from pathlib import Path

import httpx
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent import approvals
from app.agent.agent import MyAgent
from app.agent.workflows import restart_orphaned_tasks
from app.models import ActionPlan, ExecutionReceipt, Operation, Task, User
from app.models.enums import OperationStatus, TaskStatus
from app.security import hash_password
from tests.conftest import WaitingLauncher, login, make_client
from tests.fake_service import BASE_URL, FakeExternalService
from tests.fake_tools import FakeExternalCounter
from tests.test_agent_run import send
from tests.test_approvals import approve, external_then_answer, task_detail

# ---- 가짜 서비스 자체 ----


async def post_action(service: FakeExternalService, key: str, value: int) -> httpx.Response:
    async with service.client() as client:
        return await client.post("/actions", json={"value": value}, headers={"Idempotency-Key": key})


async def test_same_idempotency_key_makes_one_effect(tmp_path: Path) -> None:
    service = FakeExternalService(tmp_path / "s.sqlite3")

    first = await post_action(service, "op-1", 7)
    second = await post_action(service, "op-1", 7)
    reused = await post_action(service, "op-1", 8)

    assert (first.status_code, first.json()["duplicate"]) == (201, False)
    assert (second.status_code, second.json()["duplicate"]) == (200, True)
    assert second.json()["id"] == first.json()["id"]
    assert reused.status_code == 409
    assert [e["key"] for e in service.effects()] == ["op-1"]


async def test_records_survive_new_service_instance(tmp_path: Path) -> None:
    """서비스(그리고 앱)를 다시 만들어도 처리 기록이 남는다. 같은 키를 다시 보내도 효과는 늘지 않는다."""
    path = tmp_path / "s.sqlite3"
    await post_action(FakeExternalService(path), "op-1", 7)

    restarted = FakeExternalService(path)
    again = await post_action(restarted, "op-1", 7)

    assert again.json()["duplicate"] is True
    assert len(restarted.effects()) == 1


async def test_lookup_by_key(tmp_path: Path) -> None:
    service = FakeExternalService(tmp_path / "s.sqlite3")
    await post_action(service, "op-1", 7)

    async with service.client() as client:
        found = await client.get("/actions/op-1")
        missing = await client.get("/actions/op-2")
        no_key = await client.post(f"{BASE_URL}/actions", json={"value": 1})

    assert found.status_code == 200 and found.json()["value"] == 7
    assert missing.status_code == 404
    assert no_key.status_code == 400


# ---- 승인 경로의 외부 효과 ----


async def test_no_external_call_before_approval(
    client: httpx.AsyncClient,
    agent: MyAgent,
    conversation: int,
    fake_service: FakeExternalService,
    fake_external_counter: FakeExternalCounter,
) -> None:
    with agent.override(model=external_then_answer().model()):
        task = await send(client, conversation, "외부 도구 실행해줘")

    assert task["status"] == "waiting_approval"
    assert fake_service.effects() == []


async def test_double_approve_makes_one_effect_keyed_by_operation(
    client: httpx.AsyncClient,
    agent: MyAgent,
    conversation: int,
    db_session: AsyncSession,
    fake_service: FakeExternalService,
    fake_external_counter: FakeExternalCounter,
) -> None:
    with agent.override(model=external_then_answer().model()):
        task = await send(client, conversation, "외부 도구 실행해줘")
        await approve(client, task["approvals"][0]["id"])
        await approve(client, task["approvals"][0]["id"])

    plan = await db_session.scalar(select(ActionPlan).where(ActionPlan.task_id == task["id"]))
    assert plan is not None
    receipt = await db_session.scalar(
        select(ExecutionReceipt)
        .join(Operation, Operation.id == ExecutionReceipt.operation_id)
        .where(Operation.plan_id == plan.id)
    )
    effects = fake_service.effects()
    assert [e["key"] for e in effects] == [str(plan.operation_id)]
    assert receipt is not None and receipt.external_resource_id == str(effects[0]["id"])
    assert (await task_detail(client, task["id"]))["status"] == "done"


async def test_concurrent_approve_makes_one_effect(
    committed_app: FastAPI, fake_service: FakeExternalService, fake_external_counter: FakeExternalCounter
) -> None:
    agent: MyAgent = committed_app.state.agent
    factory = committed_app.state.session_factory
    async with factory() as session:
        session.add(User(email="race-service@example.com", password_hash=hash_password("password-race")))
        await session.commit()

    async with make_client(committed_app) as client:
        assert (await login(client, "race-service@example.com", "password-race")).status_code == 200
        conversation = (await client.post("/api/conversations", json={})).json()["id"]
        with agent.override(model=external_then_answer().model()):
            task = await send(client, conversation, "외부 도구 실행해줘")
            approval_id = task["approvals"][0]["id"]
            await asyncio.gather(approve(client, approval_id), approve(client, approval_id))
        detail = await task_detail(client, task["id"])

    assert len(fake_service.effects()) == 1
    assert detail["status"] == "done"


async def test_resume_recovered_after_crash_before_resume_registration(
    client: httpx.AsyncClient,
    agent: MyAgent,
    conversation: int,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    logged_in_user: User,
    fake_service: FakeExternalService,
    fake_external_counter: FakeExternalCounter,
) -> None:
    """승인 커밋 직후, 재개 워크플로 등록 전에 서버가 죽은 상태 → 서버 시작 때 이어서 실행(BUILD_SPEC 15.12)."""
    user_id = logged_in_user.id
    with agent.override(model=external_then_answer().model()):
        task = await send(client, conversation, "외부 도구 실행해줘")
        # 승인 API 대신 결정만 커밋한다(재개 워크플로를 시작하지 않음 = 그 직후 서버가 죽음).
        await approvals.decide(db_session, task["approvals"][0]["id"], user_id, approve=True)
        assert fake_service.effects() == []

        await restart_orphaned_tasks(WaitingLauncher(), session_factory)  # 서버 다시 시작
        await restart_orphaned_tasks(WaitingLauncher(), session_factory)  # 한 번 더 시작해도

    db_session.expire_all()
    stored = await db_session.get_one(Task, task["id"])
    operation = await db_session.scalar(
        select(Operation).join(ActionPlan, ActionPlan.id == Operation.plan_id).where(ActionPlan.task_id == task["id"])
    )
    assert stored.status == TaskStatus.DONE
    assert operation is not None and operation.status == OperationStatus.SUCCEEDED
    assert len(fake_service.effects()) == 1
