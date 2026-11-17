"""6단계: 1분 검사(run_check)가 예약 작업 실행, 감시 판정, 만료 승인 정리, 아침 브리핑 기본 예약을 하는지.

시간은 run_check(services, now)의 now로 주입한다. 예약·감시 실행은 실제 DBOS 워크플로("schedule-…", "watch-…")를
거치고, 테스트의 시작기(WaitingLauncher)가 끝날 때까지 기다린다. 모델은 가짜(FunctionModel)만 쓴다.

테스트 5: origin=schedule인 task에는 read_tools만 전달된다(test_scheduled_run_*).
테스트 14: schedule·watch task는 approvals를 만들 수 없다(test_automation_tasks_cannot_create_approvals_*).
"""

from dataclasses import replace
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from typing import Any

import httpx
import pytest
from pydantic import SecretStr
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent import approvals
from app.agent.agent import MyAgent
from app.agent.deps import AgentDeps
from app.agent.guard import GuardAgent
from app.agent.plans import PlanDraft
from app.agent.prompts import automation_instruction
from app.agent.runner import RunServices, run_task
from app.agent.toolsets import TOOLSETS_BY_ORIGIN, build_external_toolset, read_tools
from app.agent.workflows import automation_check_workflow, automation_schedule, schedule_workflow_id
from app.automation import rules, store
from app.automation.check import AutomationServices, run_check
from app.automation.watch import WatchAgent
from app.config import Settings
from app.models import (
    Approval,
    Conversation,
    Message,
    PushSubscription,
    ScheduledJob,
    Step,
    Task,
    Todo,
    UsageLog,
    User,
    Watch,
)
from app.models.enums import (
    ApprovalStatus,
    MessageRole,
    RiskLevel,
    StepStatus,
    TaskOrigin,
    TaskStatus,
    UsagePurpose,
    VerdictKind,
)
from app.push import PushNotifier
from app.users import create_user_if_missing
from tests.conftest import spent_today, tool_context
from tests.fake_push import FakePushSender
from tests.fake_tools import FakeExternalCounter, fake_external
from tests.model_scripts import call, scripted, text

EVERY_DAY = list(range(7))
READ_TOOL_NAMES = sorted(read_tools.tools)


@pytest.fixture
def push_on(test_settings: Settings) -> None:
    test_settings.vapid_public_key = "test-public-key"
    test_settings.vapid_private_key = SecretStr("test-private-key")
    test_settings.vapid_subject = "mailto:test@example.com"


async def subscribe(db_session: AsyncSession, user: User) -> None:
    db_session.add(
        PushSubscription(user_id=user.id, endpoint="https://fcm.googleapis.com/fcm/send/x", p256dh="p", auth="a")
    )
    await db_session.commit()


async def add_job(db_session: AsyncSession, user: User, next_run_at: datetime, **overrides: Any) -> ScheduledJob:
    values: dict[str, Any] = {
        "user_id": user.id,
        "title": "할 일 요약",
        "prompt_text": "오늘 할 일 요약해줘",
        "time_of_day": time(9, 0),
        "weekdays": EVERY_DAY,
        "next_run_at": next_run_at,
    }
    values.update(overrides)
    job = ScheduledJob(**values)
    db_session.add(job)
    await db_session.commit()
    return job


async def add_watch(db_session: AsyncSession, user: User, now: datetime, **overrides: Any) -> Watch:
    values: dict[str, Any] = {
        "user_id": user.id,
        "description": "할 일 개수 감시",
        "tool_name": "list_todos",
        "tool_input": {},
        "condition_text": "미완료 할 일이 2개 이상",
        "interval_minutes": 60,
        "next_run_at": now - timedelta(minutes=1),
        "expires_at": now + timedelta(days=30),
        "repeat": False,
    }
    values.update(overrides)
    watch = Watch(**values)
    db_session.add(watch)
    await db_session.commit()
    return watch


async def refreshed[T](db_session: AsyncSession, model: type[T], row_id: int) -> T:
    row = await db_session.get(model, row_id, populate_existing=True)
    assert row is not None
    return row


async def tasks_of(db_session: AsyncSession, user: User) -> list[Task]:
    return list(await db_session.scalars(select(Task).where(Task.user_id == user.id).order_by(Task.id)))


def verdict_model(met: bool, summary: str, seen: list[list[ModelMessage]] | None = None) -> FunctionModel:
    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if seen is not None:
            seen.append(list(messages))
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {"met": met, "summary": summary})])

    return FunctionModel(respond, model_name="watch-scripted")


def broken_verdict_model() -> FunctionModel:
    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart("판정 형식이 아님")])

    return FunctionModel(respond, model_name="watch-broken")


# ---- 시각 계산 ----


def test_next_schedule_run_uses_seoul_time_and_weekdays() -> None:
    after = datetime(2026, 11, 17, 21, 0, tzinfo=rules.SEOUL)  # 화요일 21:00
    assert rules.next_schedule_run(time(21, 0), [1], after) == datetime(2026, 11, 24, 21, 0, tzinfo=rules.SEOUL)
    assert rules.next_schedule_run(time(21, 1), [1], after) == datetime(2026, 11, 17, 21, 1, tzinfo=rules.SEOUL)
    assert rules.next_schedule_run(time(7, 30), [6], after) == datetime(2026, 11, 22, 7, 30, tzinfo=rules.SEOUL)
    assert rules.next_schedule_run(time(7, 30), EVERY_DAY, after) == datetime(2026, 11, 18, 7, 30, tzinfo=rules.SEOUL)


def test_repeat_text() -> None:
    assert rules.repeat_text(time(7, 30), EVERY_DAY) == "매일 07:30"
    assert rules.repeat_text(time(21, 0), [6]) == "매주 일요일 21:00"
    assert rules.repeat_text(time(9, 0), [0, 1, 2, 3, 4]) == "평일 09:00"
    assert rules.repeat_text(time(9, 0), [4, 0]) == "매주 월·금요일 09:00"


def test_checker_schedule_runs_every_minute_without_backfill() -> None:
    config = automation_schedule()

    assert config.get("schedule") == "* * * * *"
    assert config.get("automatic_backfill") is False  # 서버가 꺼져 있던 동안의 분을 몰아 실행하지 않는다
    assert config.get("workflow_fn") is automation_check_workflow


def test_schedule_workflow_id_has_job_and_time() -> None:
    run_at = datetime(2026, 11, 17, 22, 30, tzinfo=UTC)

    assert schedule_workflow_id(3, run_at) == "schedule-3-20261117T223000.000000Z"
    assert schedule_workflow_id(3, run_at.astimezone(rules.SEOUL)) == schedule_workflow_id(3, run_at)


async def test_automation_instruction_only_for_automatic_tasks(tool_deps: AgentDeps) -> None:
    chat = tool_context(tool_deps, "x")
    schedule = tool_context(replace(tool_deps, origin=TaskOrigin.SCHEDULE), "x")

    assert automation_instruction(chat) == ""
    assert "[제안]" in automation_instruction(schedule)


# ---- 예약 작업 ----


async def test_scheduled_run_creates_read_only_task_in_schedule_conversation(
    automation_services: AutomationServices,
    agent: MyAgent,
    db_session: AsyncSession,
    logged_in_user: User,
    push_sender: FakePushSender,
    push_on: None,
) -> None:
    """테스트 5: 예약 작업 task는 READ 도구만 받는다. 사용자 메시지 없이 시작하고 '예약 작업' 대화방에 답을 남긴다."""
    await subscribe(db_session, logged_in_user)
    now = datetime.now(UTC)
    due = now - timedelta(minutes=1)
    job = await add_job(db_session, logged_in_user, due)
    model = scripted(call("list_todos"), text("미완료 할 일이 없습니다.\n[제안] 내일 할 일 정리해줘"))

    with agent.override(model=model.model()):
        report = await run_check(automation_services, now)

    assert report.schedules_started == 1
    assert model.seen_tool_names == [READ_TOOL_NAMES, READ_TOOL_NAMES]
    [task] = await tasks_of(db_session, logged_in_user)
    assert (task.origin, task.status, task.trigger_message_id) == (TaskOrigin.SCHEDULE, TaskStatus.DONE, None)
    assert task.workflow_id is not None and task.workflow_id.startswith(f"schedule-{job.id}-")
    conversation = await db_session.get_one(Conversation, task.conversation_id)
    assert conversation.title == rules.AUTOMATION_CONVERSATION_TITLE
    messages = list(await db_session.scalars(select(Message).where(Message.conversation_id == conversation.id)))
    assert [(m.role, m.task_id) for m in messages] == [(MessageRole.ASSISTANT, task.id)]
    assert "[제안] 내일 할 일 정리해줘" in messages[0].content
    job = await refreshed(db_session, ScheduledJob, job.id)
    assert (job.last_run_at, job.last_task_id) == (due, task.id)
    assert job.next_run_at > now
    assert [p["kind"] for p in push_sender.payloads()] == ["task_done"]
    assert push_sender.payloads()[0]["body"] == "맡기신 작업이 끝났습니다"


async def test_scheduled_run_does_not_inherit_previous_runs(
    automation_services: AutomationServices, agent: MyAgent, db_session: AsyncSession, logged_in_user: User
) -> None:
    """예약 실행은 서로 독립이다. 같은 대화방의 이전 실행 기록(외부 글이 섞였을 수 있다)을 모델에 넘기지 않는다."""
    now = datetime.now(UTC)
    job = await add_job(db_session, logged_in_user, now - timedelta(minutes=1))
    with agent.override(model=scripted(text("첫 번째 결과")).model()):
        await run_check(automation_services, now)
    job = await refreshed(db_session, ScheduledJob, job.id)
    job.next_run_at = now
    await db_session.commit()
    second = scripted(text("두 번째 결과"))

    with agent.override(model=second.model()):
        await run_check(automation_services, now + timedelta(minutes=1))

    assert len(second.seen_messages[0]) == 1  # 이번 프롬프트 하나뿐


async def test_same_schedule_time_runs_only_once(
    automation_services: AutomationServices, agent: MyAgent, db_session: AsyncSession, logged_in_user: User
) -> None:
    now = datetime.now(UTC)
    due = now - timedelta(minutes=1)
    job = await add_job(db_session, logged_in_user, due)
    model = scripted(text("요약"))

    with agent.override(model=model.model()):
        await run_check(automation_services, now)
        await run_check(automation_services, now)  # 같은 분에 검사가 두 번 돌아도
        await automation_services.launcher.start_schedule(job.id, due, now)  # 같은 예약 시각을 다시 시작해도

    assert len(await tasks_of(db_session, logged_in_user)) == 1
    assert model.calls == 1


async def test_missed_runs_are_not_replayed_in_bulk(
    automation_services: AutomationServices, agent: MyAgent, db_session: AsyncSession, logged_in_user: User
) -> None:
    """서버가 사흘 꺼져 있었다: 밀린 실행을 몰아 돌리지 않고(2시간 넘게 지난 실행은 건너뜀) 다음 시각으로 넘긴다."""
    now = datetime.now(UTC)
    job = await add_job(db_session, logged_in_user, now - timedelta(days=3))
    model = scripted(text("요약"))

    with agent.override(model=model.model()):
        report = await run_check(automation_services, now)
        await run_check(automation_services, now + timedelta(minutes=1))

    assert report.schedules_started == 1  # 시작은 했지만 claim에서 건너뛴다
    assert await tasks_of(db_session, logged_in_user) == []
    assert model.calls == 0
    job = await refreshed(db_session, ScheduledJob, job.id)
    assert now < job.next_run_at <= now + timedelta(days=1)
    assert job.last_task_id is None


async def test_recently_missed_run_is_caught_up_once(
    automation_services: AutomationServices, agent: MyAgent, db_session: AsyncSession, logged_in_user: User
) -> None:
    now = datetime.now(UTC)
    job = await add_job(db_session, logged_in_user, now - timedelta(minutes=50))
    model = scripted(text("요약"))

    with agent.override(model=model.model()):
        await run_check(automation_services, now)
        await run_check(automation_services, now + timedelta(minutes=1))

    assert len(await tasks_of(db_session, logged_in_user)) == 1
    assert (await refreshed(db_session, ScheduledJob, job.id)).next_run_at > now


async def test_inactive_schedule_does_not_run(
    automation_services: AutomationServices, agent: MyAgent, db_session: AsyncSession, logged_in_user: User
) -> None:
    now = datetime.now(UTC)
    await add_job(db_session, logged_in_user, now - timedelta(minutes=1), is_active=False)
    model = scripted(text("요약"))

    with agent.override(model=model.model()):
        report = await run_check(automation_services, now)

    assert report.schedules_started == 0
    assert model.calls == 0


async def test_schedule_skips_model_when_daily_budget_is_used_up(
    automation_services: AutomationServices,
    agent: MyAgent,
    db_session: AsyncSession,
    logged_in_user: User,
    test_settings: Settings,
) -> None:
    db_session.add(spent_today(logged_in_user.id, Decimal(test_settings.daily_cost_limit_usd)))
    now = datetime.now(UTC)
    await add_job(db_session, logged_in_user, now - timedelta(minutes=1))
    model = scripted(text("요약"))

    with agent.override(model=model.model()):
        await run_check(automation_services, now)

    [task] = await tasks_of(db_session, logged_in_user)
    assert task.status == TaskStatus.BUDGET_EXCEEDED
    assert model.calls == 0


async def test_briefing_sends_briefing_push(
    automation_services: AutomationServices,
    agent: MyAgent,
    db_session: AsyncSession,
    logged_in_user: User,
    push_sender: FakePushSender,
    push_on: None,
) -> None:
    await subscribe(db_session, logged_in_user)
    now = datetime.now(UTC)
    assert await store.seed_default_schedules(db_session, logged_in_user.id, now)
    await db_session.commit()
    job = await db_session.scalar(select(ScheduledJob).where(ScheduledJob.user_id == logged_in_user.id))
    assert job is not None
    job.next_run_at = now - timedelta(minutes=1)
    await db_session.commit()

    with agent.override(model=scripted(text("오늘 일정은 없습니다.")).model()):
        await run_check(automation_services, now)

    assert [p["body"] for p in push_sender.payloads()] == ["오늘의 브리핑이 준비되었습니다"]


# ---- 아침 브리핑 기본 예약 ----


async def test_checker_creates_briefing_for_existing_accounts_once(
    automation_services: AutomationServices, agent: MyAgent, db_session: AsyncSession, logged_in_user: User
) -> None:
    now = datetime.now(UTC)
    with agent.override(model=scripted(text("x")).model()):
        report = await run_check(automation_services, now)

    jobs = list(await db_session.scalars(select(ScheduledJob).where(ScheduledJob.user_id == logged_in_user.id)))
    assert report.briefings_created >= 1
    assert [(j.title, j.time_of_day, j.weekdays, j.prompt_text, j.is_active) for j in jobs] == [
        (rules.BRIEFING_TITLE, time(7, 30), EVERY_DAY, rules.BRIEFING_PROMPT, True)
    ]
    local = jobs[0].next_run_at.astimezone(rules.SEOUL)
    assert (local.hour, local.minute) == (7, 30)

    await db_session.delete(jobs[0])  # 사용자가 지웠다
    await db_session.commit()
    with agent.override(model=scripted(text("x")).model()):
        await run_check(automation_services, now + timedelta(minutes=1))

    count = await db_session.scalar(
        select(func.count()).select_from(ScheduledJob).where(ScheduledJob.user_id == logged_in_user.id)
    )
    assert count == 0  # 지운 브리핑은 다시 생기지 않는다


async def test_new_account_gets_briefing(db_session: AsyncSession) -> None:
    user = await create_user_if_missing(db_session, "new@example.com", "password-new")
    assert user is not None

    jobs = list(await db_session.scalars(select(ScheduledJob).where(ScheduledJob.user_id == user.id)))

    assert [j.prompt_text for j in jobs] == [rules.BRIEFING_PROMPT]
    assert user.briefing_seeded_at is not None


# ---- 조건 감시 ----


async def test_watch_met_leaves_message_notifies_and_stops(
    automation_services: AutomationServices,
    watch_agent: WatchAgent,
    db_session: AsyncSession,
    logged_in_user: User,
    push_sender: FakePushSender,
    push_on: None,
) -> None:
    await subscribe(db_session, logged_in_user)
    db_session.add_all([Todo(user_id=logged_in_user.id, title="우유"), Todo(user_id=logged_in_user.id, title="빵")])
    now = datetime.now(UTC)
    watch = await add_watch(db_session, logged_in_user, now)
    seen: list[list[ModelMessage]] = []

    with watch_agent.override(model=verdict_model(True, "미완료 할 일이 2개입니다.", seen)):
        report = await run_check(automation_services, now)

    assert report.watches_started == 1
    assert "우유" in str(seen[0])  # 판정 에이전트는 도구 결과를 받는다
    watch = await refreshed(db_session, Watch, watch.id)
    assert watch.is_active is False  # repeat=false면 끈다
    assert watch.last_notified_at is not None
    assert watch.last_result is not None and watch.last_result["status"] == "met"
    [message] = list(
        await db_session.scalars(select(Message).join(Conversation).where(Conversation.user_id == logged_in_user.id))
    )
    assert "미완료 할 일이 2개입니다." in message.content and "할 일 개수 감시" in message.content
    assert message.task_id is None
    assert [p["kind"] for p in push_sender.payloads()] == ["watch_met"]
    [usage] = list(await db_session.scalars(select(UsageLog).where(UsageLog.user_id == logged_in_user.id)))
    assert (usage.purpose, usage.task_id, usage.model) == (UsagePurpose.WATCH, None, "watch-scripted")
    assert await tasks_of(db_session, logged_in_user) == []  # 감시 확인은 task를 만들지 않는다


async def test_repeating_watch_waits_12_hours_after_met(
    automation_services: AutomationServices, watch_agent: WatchAgent, db_session: AsyncSession, logged_in_user: User
) -> None:
    now = datetime.now(UTC)
    watch = await add_watch(db_session, logged_in_user, now, repeat=True)

    with watch_agent.override(model=verdict_model(True, "충족")):
        await run_check(automation_services, now)

    watch = await refreshed(db_session, Watch, watch.id)
    assert watch.is_active is True
    assert watch.next_run_at == now + timedelta(hours=12)


async def test_watch_not_met_checks_again_after_interval(
    automation_services: AutomationServices,
    watch_agent: WatchAgent,
    db_session: AsyncSession,
    logged_in_user: User,
    push_sender: FakePushSender,
    push_on: None,
) -> None:
    await subscribe(db_session, logged_in_user)
    now = datetime.now(UTC)
    watch = await add_watch(db_session, logged_in_user, now, interval_minutes=45)

    with watch_agent.override(model=verdict_model(False, "아직 아닙니다.")):
        await run_check(automation_services, now)
        report = await run_check(automation_services, now + timedelta(minutes=1))  # 간격 전에는 다시 보지 않는다

    watch = await refreshed(db_session, Watch, watch.id)
    assert report.watches_started == 0
    assert (watch.is_active, watch.next_run_at) == (True, now + timedelta(minutes=45))
    assert watch.last_result is not None and watch.last_result["status"] == "not_met"
    assert push_sender.sent == []


async def test_watch_verdict_format_error_is_not_met(
    automation_services: AutomationServices, watch_agent: WatchAgent, db_session: AsyncSession, logged_in_user: User
) -> None:
    now = datetime.now(UTC)
    watch = await add_watch(db_session, logged_in_user, now)

    with watch_agent.override(model=broken_verdict_model()):
        await run_check(automation_services, now)

    watch = await refreshed(db_session, Watch, watch.id)
    assert watch.is_active is True
    assert watch.last_result is not None and watch.last_result["status"] == "judge_failed"


async def test_watch_tool_failure_skips_verdict(
    automation_services: AutomationServices, watch_agent: WatchAgent, db_session: AsyncSession, logged_in_user: User
) -> None:
    """카카오 키가 없어 날씨 조회가 실패하면 판정 모델을 부르지 않는다(비용 없음)."""
    now = datetime.now(UTC)
    watch = await add_watch(
        db_session, logged_in_user, now, tool_name="get_weather", tool_input={"place": "서울", "date": "2026-11-18"}
    )
    seen: list[list[ModelMessage]] = []

    with watch_agent.override(model=verdict_model(True, "x", seen)):
        await run_check(automation_services, now)

    watch = await refreshed(db_session, Watch, watch.id)
    assert seen == []
    assert watch.last_result is not None and watch.last_result["status"] == "tool_failed"
    assert watch.is_active is True


async def test_watch_never_runs_a_non_read_tool_even_if_stored(
    automation_services: AutomationServices, watch_agent: WatchAgent, db_session: AsyncSession, logged_in_user: User
) -> None:
    """DB에 READ가 아닌 도구가 적혀 있어도(조작·버그) 실행 때 다시 막는다."""
    now = datetime.now(UTC)
    watch = await add_watch(db_session, logged_in_user, now, tool_name="add_todo", tool_input={"title": "해킹"})

    with watch_agent.override(model=verdict_model(True, "x")):
        await run_check(automation_services, now)

    todos = await db_session.scalar(select(func.count()).select_from(Todo).where(Todo.user_id == logged_in_user.id))
    watch = await refreshed(db_session, Watch, watch.id)
    assert todos == 0
    assert watch.last_result is not None and watch.last_result["status"] == "tool_failed"


async def test_watch_skipped_when_daily_budget_is_used_up(
    automation_services: AutomationServices,
    watch_agent: WatchAgent,
    db_session: AsyncSession,
    logged_in_user: User,
    test_settings: Settings,
) -> None:
    db_session.add(spent_today(logged_in_user.id, Decimal(test_settings.daily_cost_limit_usd)))
    now = datetime.now(UTC)
    watch = await add_watch(db_session, logged_in_user, now)
    seen: list[list[ModelMessage]] = []

    with watch_agent.override(model=verdict_model(True, "x", seen)):
        await run_check(automation_services, now)

    watch = await refreshed(db_session, Watch, watch.id)
    assert seen == []
    assert watch.last_result is not None and watch.last_result["status"] == "skipped_budget"
    assert watch.next_run_at == now + timedelta(minutes=60)


async def test_expired_watch_is_turned_off(
    automation_services: AutomationServices, watch_agent: WatchAgent, db_session: AsyncSession, logged_in_user: User
) -> None:
    now = datetime.now(UTC)
    watch = await add_watch(
        db_session, logged_in_user, now, expires_at=now - timedelta(minutes=1), next_run_at=now + timedelta(hours=1)
    )

    with watch_agent.override(model=verdict_model(True, "x")):
        await run_check(automation_services, now)

    watch = await refreshed(db_session, Watch, watch.id)
    assert watch.is_active is False
    assert watch.last_result is not None and watch.last_result["status"] == "expired"


# ---- 만료 승인 정리 ----


async def test_checker_expires_overdue_approvals(
    automation_services: AutomationServices, db_session: AsyncSession, logged_in_user: User
) -> None:
    now = datetime.now(UTC)
    conversation = Conversation(user_id=logged_in_user.id, title="대화")
    db_session.add(conversation)
    await db_session.flush()
    task = Task(
        conversation_id=conversation.id,
        user_id=logged_in_user.id,
        origin=TaskOrigin.CHAT,
        status=TaskStatus.WAITING_APPROVAL,
    )
    db_session.add(task)
    await db_session.flush()
    step = Step(
        task_id=task.id,
        order=1,
        tool_name="create_event",
        tool_call_id="call-expire",
        risk_level=RiskLevel.EXTERNAL,
        input={},
        status=StepStatus.WAITING_APPROVAL,
        label="일정 만드는 중",
    )
    db_session.add(step)
    await db_session.flush()
    approval = Approval(step_id=step.id, tool_call_id="call-expire", summary="x", expires_at=now - timedelta(minutes=1))
    db_session.add(approval)
    await db_session.commit()

    report = await run_check(automation_services, now)

    assert report.approvals_expired == 1
    assert (await refreshed(db_session, Task, task.id)).status == TaskStatus.CANCELLED
    assert (await refreshed(db_session, Approval, approval.id)).status == ApprovalStatus.EXPIRED
    assert (await refreshed(db_session, Step, step.id)).status == StepStatus.EXPIRED


# ---- 테스트 14 ----


@pytest.mark.parametrize("origin", [TaskOrigin.SCHEDULE, TaskOrigin.WATCH])
async def test_automation_tasks_cannot_create_approvals_in_store(db_session: AsyncSession, origin: TaskOrigin) -> None:
    draft = PlanDraft("create_event", "1", {}, "0" * 64, connection_id=None, target_resource_id=None)
    request = approvals.ApprovalRequest(
        tool_call_id="call-x",
        plan=draft,
        label="x",
        summary="x",
        verdict=VerdictKind.MATCH,
        reason="x",
        guard_model="x",
    )
    with pytest.raises(ValueError):
        await approvals.save_requests(db_session, task_id=1, user_id=1, origin=origin, requests=[request])


@pytest.mark.parametrize("origin", [TaskOrigin.SCHEDULE, TaskOrigin.WATCH])
async def test_automation_tasks_cannot_create_approvals_even_if_misconfigured(
    agent: MyAgent,
    guard: GuardAgent,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    test_settings: Settings,
    logged_in_user: User,
    fake_external_counter: FakeExternalCounter,
    monkeypatch: pytest.MonkeyPatch,
    origin: TaskOrigin,
) -> None:
    """설정 실수로 자동 작업에 EXTERNAL 도구가 들어가 모델이 불러도 승인 요청은 생기지 않고 task는 실패한다."""
    monkeypatch.setitem(TOOLSETS_BY_ORIGIN, origin, [read_tools, build_external_toolset([fake_external])])
    conversation = Conversation(user_id=logged_in_user.id, title=rules.AUTOMATION_CONVERSATION_TITLE)
    db_session.add(conversation)
    await db_session.flush()
    task = Task(conversation_id=conversation.id, user_id=logged_in_user.id, origin=origin)
    db_session.add(task)
    await db_session.commit()
    services = RunServices(
        agent=agent,
        guard=guard,
        session_factory=session_factory,
        settings=test_settings,
        notifier=PushNotifier(session_factory, test_settings, FakePushSender()),
        http=httpx.AsyncClient(),
    )

    with agent.override(model=scripted(call("fake_external", value=1), text("완료")).model()):
        await run_task(services, task.id, prompt="외부 행동 해줘")

    approval_count = await db_session.scalar(
        select(func.count()).select_from(Approval).join(Step).where(Step.task_id == task.id)
    )
    assert approval_count == 0
    assert (await refreshed(db_session, Task, task.id)).status == TaskStatus.FAILED
    assert fake_external_counter.calls == []
