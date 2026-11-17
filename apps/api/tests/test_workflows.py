"""3단계 내구성 실행 테스트 (SPEC 테스트 9). 실제 모델 대신 등록된 FunctionModel, 외부 도구 대신 fake_external.

- 테스트 9: test_same_task_workflow_started_twice_runs_once
- 바로 응답: test_send_message_returns_before_run_finishes, test_approve_returns_before_resume_finishes
- 복구 재생: test_replayed_workflow_does_not_repeat_tools_or_results (DBOS fork로 실제 재실행),
            test_recorded_*/test_interrupted_* (StepTracking의 tool_call_id 처리)
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from decimal import Decimal

import httpx
from dbos import DBOS
from fastapi import FastAPI
from pydantic import SecretStr
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent import runner
from app.agent.agent import MyAgent
from app.agent.results import ToolResult
from app.agent.tracking import UNCERTAIN_MESSAGE
from app.agent.workflows import (
    WorkflowLauncher,
    restart_orphaned_tasks,
    resume_workflow_id,
    run_workflow_id,
    services_from_state,
    start_resume_if_waiting,
)
from app.config import Settings
from app.models import ActionPlan, Approval, Conversation, Message, Operation, Step, Task, Todo, User
from app.models.enums import (
    ApprovalStatus,
    MessageRole,
    OperationStatus,
    RiskLevel,
    StepStatus,
    TaskOrigin,
    TaskStatus,
)
from app.security import hash_password
from tests.conftest import WaitingLauncher, login, make_client, message_body
from tests.fake_tools import FakeExternalCounter
from tests.model_scripts import call, current_run_messages, registered, scripted, text
from tests.test_agent_run import send


async def make_queued_task(db_session: AsyncSession, user: User, content: str = "우유 사기 할 일에 추가해줘") -> Task:
    conversation = Conversation(user_id=user.id, title="워크플로")
    db_session.add(conversation)
    await db_session.flush()
    message = Message(conversation_id=conversation.id, role=MessageRole.USER, content=content)
    db_session.add(message)
    await db_session.flush()
    task = Task(conversation_id=conversation.id, user_id=user.id, trigger_message_id=message.id, origin=TaskOrigin.CHAT)
    db_session.add(task)
    await db_session.flush()
    message.task_id = task.id
    await db_session.commit()
    return task


async def count(session: AsyncSession, model: type, *conditions: object) -> int:
    return await session.scalar(select(func.count()).select_from(model).where(*conditions)) or 0  # type: ignore[arg-type]


def has_tool_return(messages: list[ModelMessage]) -> bool:
    return any(isinstance(p, ToolReturnPart) for m in current_run_messages(messages) for p in m.parts)


# ---- 테스트 9: 같은 task의 워크플로를 두 번 시작해도 한 번만 실행된다 ----


async def test_same_task_workflow_started_twice_runs_once(
    app: FastAPI, agent: MyAgent, db_session: AsyncSession, logged_in_user: User
) -> None:
    task = await make_queued_task(db_session, logged_in_user)
    model = scripted(call("add_todo", title="우유 사기"), text("추가했습니다."))
    launcher = WorkflowLauncher()  # 기다리지 않는 실제 시작기

    with agent.override(model=model.model()):
        first = await launcher.start_run(task.id, "우유 사기 할 일에 추가해줘")
        second = await launcher.start_run(task.id, "우유 사기 할 일에 추가해줘")  # 같은 ID로 한 번 더
        await first.get_result()
        await second.get_result()
        third = await launcher.start_run(task.id, "우유 사기 할 일에 추가해줘")  # 끝난 뒤에 또
        await third.get_result()

    assert first.get_workflow_id() == second.get_workflow_id() == run_workflow_id(task.id)
    assert model.calls == 2  # 도구 호출 1번 + 최종 답변 1번. 두 번째·세 번째 시작은 실행하지 않았다
    assert await count(db_session, Todo, Todo.user_id == logged_in_user.id) == 1
    assert await count(db_session, Step, Step.task_id == task.id) == 1
    assert await count(db_session, Message, Message.task_id == task.id, Message.role == MessageRole.ASSISTANT) == 1
    stored = await db_session.scalar(select(Task).where(Task.id == task.id).execution_options(populate_existing=True))
    assert stored is not None and stored.status == TaskStatus.DONE
    assert stored.workflow_id == run_workflow_id(task.id)


# ---- 워크플로 ID ----


def test_resume_workflow_id_counts_rounds() -> None:
    assert resume_workflow_id(5, None) == "task-5-resume-1"
    assert resume_workflow_id(5, "task-5") == "task-5-resume-1"
    assert resume_workflow_id(5, "task-5-resume-1") == "task-5-resume-2"
    assert resume_workflow_id(5, "task-5-resume-12") == "task-5-resume-13"


async def test_each_approval_round_gets_its_own_resume_workflow(
    client: httpx.AsyncClient,
    agent: MyAgent,
    conversation: int,
    db_session: AsyncSession,
    fake_external_counter: FakeExternalCounter,
) -> None:
    model = scripted(call("fake_external", value=1), call("fake_external", value=2), text("둘 다 했습니다."))
    with agent.override(model=model.model()):
        task = await send(client, conversation, "외부 도구 두 번")
        await client.post(f"/api/approvals/{task['approvals'][0]['id']}/approve")
        middle = (await client.get(f"/api/tasks/{task['id']}")).json()
        middle_workflow = await db_session.scalar(
            select(Task.workflow_id).where(Task.id == task["id"]).execution_options(populate_existing=True)
        )
        await client.post(f"/api/approvals/{middle['approvals'][1]['id']}/approve")

    final = await db_session.scalar(select(Task).where(Task.id == task["id"]).execution_options(populate_existing=True))
    assert middle["status"] == "waiting_approval"
    assert middle_workflow == f"task-{task['id']}-resume-1"
    assert final is not None and final.status == TaskStatus.DONE
    assert final.workflow_id == f"task-{task['id']}-resume-2"
    assert fake_external_counter.count == 2


# ---- 바로 응답 (실제 커밋, 기다리지 않는 시작기) ----


@asynccontextmanager
async def committed_login(application: FastAPI, email: str) -> AsyncIterator[httpx.AsyncClient]:
    async with application.state.session_factory() as session:
        session.add(User(email=email, password_hash=hash_password("password-x")))
        await session.commit()
    async with make_client(application) as client:
        assert (await login(client, email, "password-x")).status_code == 200
        yield client


async def test_send_message_returns_before_run_finishes(committed_app: FastAPI) -> None:
    committed_app.state.launcher = WorkflowLauncher()
    agent: MyAgent = committed_app.state.agent
    gate = asyncio.Event()

    async def slow_answer(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        await gate.wait()
        return ModelResponse(parts=[text("늦게 답합니다.")])

    async with committed_login(committed_app, "fast@example.com") as client:
        conversation = (await client.post("/api/conversations", json={})).json()["id"]
        with agent.override(model=registered(slow_answer)):
            response = await client.post(f"/api/conversations/{conversation}/messages", json=message_body("안녕"))
            task_id = response.json()["task_id"]
            during = (await client.get(f"/api/tasks/{task_id}")).json()
            gate.set()
            await (await DBOS.retrieve_workflow_async(run_workflow_id(task_id))).get_result()
        after = (await client.get(f"/api/tasks/{task_id}")).json()

    assert response.status_code == 202
    assert during["status"] in {"queued", "running"}
    assert after["status"] == "done"
    assert after["final_answer"] == "늦게 답합니다."


async def test_approve_returns_before_resume_finishes(
    committed_app: FastAPI, fake_external_counter: FakeExternalCounter
) -> None:
    agent: MyAgent = committed_app.state.agent
    gate = asyncio.Event()

    async def external_then_slow_answer(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if not has_tool_return(messages):
            return ModelResponse(parts=[ToolCallPart("fake_external", {"value": 3}, "call-slow")])
        await gate.wait()
        return ModelResponse(parts=[text("처리했습니다.")])

    async with committed_login(committed_app, "approve-fast@example.com") as client:
        conversation = (await client.post("/api/conversations", json={})).json()["id"]
        with agent.override(model=registered(external_then_slow_answer)):
            task = await send(client, conversation, "외부 도구 실행해줘")  # 첫 run은 기다리는 시작기
            committed_app.state.launcher = WorkflowLauncher()
            response = await client.post(f"/api/approvals/{task['approvals'][0]['id']}/approve")
            gate.set()
            resume_id = resume_workflow_id(task["id"], run_workflow_id(task["id"]))
            await (await DBOS.retrieve_workflow_async(resume_id)).get_result()
        after = (await client.get(f"/api/tasks/{task['id']}")).json()

    assert response.status_code == 200
    assert response.json()["changed"] is True
    assert response.json()["task_status"] in {"waiting_approval", "running"}
    assert after["status"] == "done"
    assert fake_external_counter.count == 1


# ---- 복구: 워크플로가 다시 실행돼도 도구·결과를 반복하지 않는다 ----


async def test_replayed_workflow_does_not_repeat_tools_or_results(
    committed_app: FastAPI, test_settings: Settings
) -> None:
    """DBOS fork로 끝난 워크플로를 '결과 저장 단계'부터 다시 실행한다(서버가 그 직전에 죽은 상황과 같다).

    앞 단계(claim, prepare, 모델 응답)는 기록된 결과가 재생되고, add_todo는 워크플로 코드라 다시 불리지만
    StepTracking이 끝난 호출로 보고 실행하지 않는다. 결과 저장은 task가 이미 끝나 아무것도 하지 않는다.

    fork한 워크플로는 DBOS의 다른 스레드에서 돌아 테스트의 agent.override가 닿지 않는다. 운영 복구처럼 설정의 모델
    문자열로 모델 객체를 만들게 되므로 가짜 키를 넣는다. 모델 응답은 기록된 것이 재생되어 실제 요청은 없다
    (테스트는 실제 모델 요청을 전역으로 막고 있다).
    """
    test_settings.anthropic_api_key = SecretStr("sk-test-not-a-real-key")
    agent: MyAgent = committed_app.state.agent
    async with committed_login(committed_app, "replay@example.com") as client:
        conversation = (await client.post("/api/conversations", json={})).json()["id"]
        with agent.override(model=scripted(call("add_todo", title="우유 사기"), text("추가했습니다.")).model()):
            task = await send(client, conversation, "우유 사기 할 일에 추가해줘")
        steps = await DBOS.list_workflow_steps_async(run_workflow_id(task["id"]))
        finish_step = next(s["function_id"] for s in steps if s["function_name"] == "finish_task")
        forked = await DBOS.fork_workflow_async(run_workflow_id(task["id"]), finish_step)
        await forked.get_result()
        messages = (await client.get(f"/api/conversations/{conversation}/messages")).json()

    async with committed_app.state.session_factory() as session:
        assert await count(session, Todo) == 1
        assert await count(session, Step, Step.task_id == task["id"]) == 1
    assert [m["role"] for m in messages] == ["user", "assistant"]


def fixed_call(tool_name: str, args: dict, tool_call_id: str, answer: str = "끝"):
    """정해진 tool_call_id로 도구를 한 번 부르고, 결과를 받으면 답하는 대본(복구 때 같은 id가 다시 오는 상황)."""

    seen: list[list[ModelMessage]] = []

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen.append(list(messages))
        if has_tool_return(messages):
            return ModelResponse(parts=[text(answer)])
        return ModelResponse(parts=[ToolCallPart(tool_name, args, tool_call_id)])

    return respond, seen


async def add_step(db_session: AsyncSession, task: Task, tool: str, risk: RiskLevel, status: StepStatus, **kw) -> Step:
    step = Step(
        task_id=task.id,
        order=1,
        tool_name=tool,
        tool_call_id="call-fixed",
        risk_level=risk,
        input=kw.pop("input", {}),
        status=status,
        label="복구 테스트",
        **kw,
    )
    db_session.add(step)
    await db_session.commit()
    return step


async def run_directly(app: FastAPI, agent: MyAgent, task: Task, respond) -> None:
    with agent.override(model=registered(respond)):
        await runner.run_task(services_from_state(app.state), task.id, prompt="복구 테스트")


async def test_recorded_internal_call_is_replayed_not_executed(
    app: FastAPI, agent: MyAgent, db_session: AsyncSession, logged_in_user: User
) -> None:
    task = await make_queued_task(db_session, logged_in_user)
    recorded = ToolResult.success({"id": 999, "title": "우유 사기", "is_done": False}).model_dump(mode="json")
    await add_step(
        db_session, task, "add_todo", RiskLevel.INTERNAL, StepStatus.DONE, input={"title": "우유 사기"}, output=recorded
    )
    respond, seen = fixed_call("add_todo", {"title": "우유 사기"}, "call-fixed")

    await run_directly(app, agent, task, respond)

    assert await count(db_session, Todo, Todo.user_id == logged_in_user.id) == 0  # 다시 넣지 않았다
    returns = [p for m in seen[-1] for p in m.parts if isinstance(p, ToolReturnPart)]
    content = returns[0].content
    assert isinstance(content, ToolResult)
    assert content.ok and content.data == recorded["data"]  # 기록된 결과를 모델에게 준다
    assert await count(db_session, Step, Step.task_id == task.id) == 1


async def test_interrupted_internal_call_is_not_executed_again(
    app: FastAPI, agent: MyAgent, db_session: AsyncSession, logged_in_user: User
) -> None:
    """INTERNAL 도구 실행 도중 서버가 죽었다면(step running) 실제로 들어갔는지 모르므로 다시 실행하지 않는다."""
    task = await make_queued_task(db_session, logged_in_user)
    step = await add_step(db_session, task, "add_todo", RiskLevel.INTERNAL, StepStatus.RUNNING, input={"title": "A"})
    respond, _ = fixed_call("add_todo", {"title": "A"}, "call-fixed")

    await run_directly(app, agent, task, respond)

    assert await count(db_session, Todo, Todo.user_id == logged_in_user.id) == 0
    await db_session.refresh(step)
    # 1단계 보강: 실행했는지 모르는 호출은 실패가 아니라 결과 불명(unknown)이다.
    assert step.status == StepStatus.UNKNOWN
    assert step.ok is False
    assert step.output is not None and step.output["user_message"] == UNCERTAIN_MESSAGE
    assert step.output["status"] == "unknown"


async def test_interrupted_read_call_runs_again(
    app: FastAPI, agent: MyAgent, db_session: AsyncSession, logged_in_user: User
) -> None:
    task = await make_queued_task(db_session, logged_in_user)
    step = await add_step(db_session, task, "list_todos", RiskLevel.READ, StepStatus.RUNNING)
    respond, _ = fixed_call("list_todos", {}, "call-fixed")

    await run_directly(app, agent, task, respond)

    await db_session.refresh(step)
    assert step.status == StepStatus.DONE
    assert step.ok is True


async def test_interrupted_external_call_is_not_executed_again(
    client: httpx.AsyncClient,
    agent: MyAgent,
    conversation: int,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    fake_external_counter: FakeExternalCounter,
) -> None:
    """at-most-once: 승인된 외부 도구가 실행 도중(begin_execution 통과, step running) 서버가 죽었다면
    복구 때 다시 실행하지 않는다. 3단계 보강: 외부 서비스에 결과를 물어 처리 기록이 없으면 '실행되지 않음'으로 확정한다
    (기록이 있거나 조회가 안 되는 경우는 tests/test_faults.py)."""
    model = scripted(call("fake_external", value=5), text("확인이 필요합니다."))
    with agent.override(model=model.model()):
        task = await send(client, conversation, "외부 도구 실행해줘")
        step_id = task["steps"][0]["id"]
        # 승인 → 도구 첫 줄 통과(step running)까지 간 뒤 서버가 죽은 상태를 만든다.
        await db_session.execute(update(Approval).where(Approval.step_id == step_id).values(status="approved"))
        await db_session.execute(update(Step).where(Step.id == step_id).values(status=StepStatus.RUNNING))
        plan_ids = select(ActionPlan.id).where(ActionPlan.step_id == step_id)
        await db_session.execute(
            update(Operation).where(Operation.plan_id.in_(plan_ids)).values(status=OperationStatus.EXECUTING)
        )
        await db_session.commit()
        await start_resume_if_waiting(WaitingLauncher(), session_factory, task["id"])

    detail = (await client.get(f"/api/tasks/{task['id']}")).json()
    assert fake_external_counter.count == 0
    assert detail["status"] == "done"
    assert detail["steps"][0]["status"] == "failed"  # 재조회로 실행되지 않았음을 확인했다
    assert detail["steps"][0]["output"]["error_code"] == "not_executed"


# ---- 재개 직전 하루 비용 상한 ----


async def test_budget_exceeded_at_resume_closes_approved_step(
    client: httpx.AsyncClient,
    agent: MyAgent,
    conversation: int,
    test_settings: Settings,
    fake_external_counter: FakeExternalCounter,
) -> None:
    with agent.override(model=scripted(call("fake_external", value=1), text("끝")).model()):
        task = await send(client, conversation, "외부 도구 실행해줘")
        test_settings.daily_cost_limit_usd = Decimal("0")
        await client.post(f"/api/approvals/{task['approvals'][0]['id']}/approve")

    detail = (await client.get(f"/api/tasks/{task['id']}")).json()
    assert fake_external_counter.count == 0
    assert detail["status"] == "budget_exceeded"
    assert detail["approvals"][0]["status"] == "approved"
    assert detail["steps"][0]["status"] == "failed"  # waiting_approval로 남지 않는다
    assert detail["steps"][0]["output"]["user_message"] == runner.NOT_EXECUTED_MESSAGE


# ---- 단계들의 멱등성 ----


async def test_claim_only_reclaims_own_running_task(
    app: FastAPI, db_session: AsyncSession, logged_in_user: User
) -> None:
    task = await make_queued_task(db_session, logged_in_user)
    services = services_from_state(app.state)

    first = await runner.claim_run(services, task.id, "task-x")
    again_same = await runner.claim_run(services, task.id, "task-x")  # 복구된 같은 워크플로
    other = await runner.claim_run(services, task.id, "task-y")
    outside = await runner.claim_run(services, task.id, None)

    assert first is not None and again_same is not None
    assert other is None and outside is None


async def test_finish_twice_saves_once(app: FastAPI, db_session: AsyncSession, logged_in_user: User) -> None:
    task = await make_queued_task(db_session, logged_in_user)
    services = services_from_state(app.state)
    info = await runner.claim_run(services, task.id, None)
    assert info is not None
    outcome = runner.Outcome(TaskStatus.DONE, final_answer="끝")

    await runner.finish(services, info, outcome, [])
    second = await runner.finish(services, info, outcome, [])

    assert second is None
    assert await count(db_session, Message, Message.task_id == task.id, Message.role == MessageRole.ASSISTANT) == 1


# ---- 서버 시작 때 시작하지 못한 task 잇기 ----


async def test_restart_orphaned_queued_task(
    app: FastAPI, agent: MyAgent, db_session: AsyncSession, session_factory, logged_in_user: User
) -> None:
    task = await make_queued_task(db_session, logged_in_user, content="안녕")
    with agent.override(model=scripted(text("네")).model()):
        started = await restart_orphaned_tasks(WaitingLauncher(), session_factory)

    stored = await db_session.scalar(select(Task).where(Task.id == task.id).execution_options(populate_existing=True))
    assert started >= 1
    assert stored is not None and stored.status == TaskStatus.DONE


async def test_resume_not_started_while_approval_pending(
    client: httpx.AsyncClient,
    agent: MyAgent,
    conversation: int,
    session_factory: async_sessionmaker[AsyncSession],
    fake_external_counter: FakeExternalCounter,
) -> None:
    with agent.override(model=scripted(call("fake_external", value=1), text("끝")).model()):
        task = await send(client, conversation, "외부 도구 실행해줘")
        await start_resume_if_waiting(WaitingLauncher(), session_factory, task["id"])

    detail = (await client.get(f"/api/tasks/{task['id']}")).json()
    assert detail["status"] == "waiting_approval"
    assert [a["status"] for a in detail["approvals"]] == [ApprovalStatus.PENDING.value]
    assert fake_external_counter.count == 0
