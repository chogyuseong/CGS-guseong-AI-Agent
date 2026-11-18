"""1분 검사와 예약·감시 실행 (명세: 예약 검사 워크플로 하나가 1분마다 scheduled_jobs와 watches를 처리하고,
만료된 승인을 정리한다). DBOS 워크플로 등록은 app/agent/workflows.py, 여기는 각 워크플로가 부르는 본문이다.

    automation_check 워크플로 (DBOS 예약 실행, 매분)
      ID는 DBOS가 "sched-automation-check-{시각}"으로 정해 같은 분이 두 번 돌지 않는다
      [check_clock]          지금 시각을 단계로 기록(workflows.py). 복구 때도 같은 now로 다시 계산한다
      run_check(now)
        [expire_approvals]   만료 시각이 지난 pending 승인 → expired, task cancelled (approvals.expire_task)
        [expire_watches]     만료일이 지난 켜진 감시 → 끔
        [find_outbox]        3단계 보강: 시작하지 못한 outbox 요청 → outbox.dispatch_events로 다시 시작
        [find_due]           next_run_at ≤ now인 켜진 예약·감시
        예약마다 run_schedule 워크플로 시작  ID "schedule-{예약 id}-{예정 시각}"
        감시마다 run_watch 워크플로 시작     ID "watch-{감시 id}-{예정 시각}"
      같은 예정 시각은 같은 워크플로 ID라 몇 번 시작해도 DBOS가 한 번만 실행한다.
        매시 17분이면 retention.prune_expired_records([prune_records] 단계 + DBOS 기록 삭제)

    run_schedule 워크플로
        [claim_schedule]  예약 행 잠금 → 켜져 있고 next_run_at이 그 예정 시각일 때만: 다음 시각 계산,
                          2시간 넘게 지난 실행이면 건너뜀, 아니면 '예약 작업' 대화방에 task(origin=schedule, queued,
                          사용자 메시지 없음) 생성, last_run_at·last_task_id 기록 → 한 트랜잭션
        runner.run_task   3단계의 실행 흐름 그대로(READ 도구만, 이전 기록 없음, 하루 비용 상한, 결과는 assistant 메시지)
                          완료 푸시: 아침 브리핑은 briefing, 그 밖은 task_done(명세: 자동 작업은 완료 때 항상)

    run_watch 워크플로 (task를 만들지 않는다)
        [claim_watch]    감시 행 잠금 → 켜져 있고 예정 시각이 맞을 때만. 다음 확인을 now + 간격으로 먼저 미룬다.
                         만료면 끔, 하루 비용 상한이면 건너뜀(last_result에 기록)
        [watch_tool]     저장된 READ 도구 실행(실행 때 다시 READ·메일 아님 검사)
        [watch_verdict]  도구가 성공했을 때만 판정 에이전트(WatchVerdict)
        [finish_watch]   last_result, usage_logs(purpose=watch, task 없음), met이면 '예약 작업' 대화방에 요약·출처
                         + repeat=false면 끔 / true면 12시간 뒤
        [notify_watch]   met이면 watch_met 푸시

[단계]는 DBOS 단계(durable_step)라 서버가 죽었다 살아나도 다시 실행하지 않는다. 워크플로 밖(테스트)에서는 그냥 실행된다.

진입점: app/agent/workflows.py의 automation_check(DBOS apply_schedules, cron "* * * * *", Asia/Seoul,
automatic_backfill=False), run_schedule, run_watch 워크플로. 시작기는 workflows.WorkflowLauncher(app.state.launcher).
근거: BUILD_SPEC 8절(예약·감시·아침 브리핑), 절대 원칙 7(예약·감시 모델에는 READ 도구만).
DECISIONS 2026-11-17 "6단계"(1분 검사와 중복·밀린 실행, 예약 실행, 감시 판정)와 "6단계 보강"(automation_occurrences,
숫자 조건, 다시 알림 정책, 예산 멈춤 표시).

변경 이력:
- 2026-11-17 6단계: 1분 검사·예약 실행·감시 실행.
- 2026-11-17 3단계 보강: 1분 검사가 밀린 outbox 요청도 다시 시작한다(find_outbox).
- 2026-11-17 5단계 보강: 매시 17분 보관 기간 정리(retention.py).
- 2026-11-17 6단계 보강: 발생 기록(automation_occurrences)으로 예정 시각마다 한 번, 숫자 조건, unknown 판정,
  거짓→참 전환 때만 알림, 예산 멈춤(paused_budget). 브리핑 자동 생성(seed_briefings) 단계 제거.

고칠 때 주의:
- durable_step 이름·순서(이 파일과 retention.py의 단계)를 바꾸면 workflows.WORKFLOW_VERSION을 올린다
  (진행 중인 워크플로가 복구 때 다른 단계 결과를 받게 된다). 단계 결과 모양(DueWork, WatchJob 등)도 마찬가지다.
- 워크플로 ID 형식(schedule-…, watch-…)은 workflows.schedule_workflow_id·watch_workflow_id에 있다. 중복 실행 방지의
  첫 겹이므로 바꾸면 이미 시작한 예정 시각이 다시 돌 수 있다.
- 발생 기록 status·skip_reason 값을 바꾸면 docs/STATE_MACHINES.md 5절을 같이 고친다.
- last_result의 키·status 값은 app/schemas/automations.py(WatchStatus·WatchResultOut, 모양이 틀리면 null로 줌)와
  화면 apps/web/lib/automations.ts(상태 문구)가 읽는다. 값을 더하면 스키마·gen:api·화면 문구를 같이 고친다.
- 테스트는 run_check(services, now)에 시각을 넣어 부르고, DBOS 예약(apply_schedules)을 등록하지 않는다
  (테스트 DB에서 매분 검사가 돌게 된다).
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent import approvals, outbox, runner
from app.agent.budget import BudgetScope, calculate_cost, daily_cost_exceeded
from app.agent.deps import AgentDeps
from app.agent.durable import durable_step
from app.agent.results import ToolResult
from app.agent.runner import RunServices
from app.automation import conditions, retention, rules, store
from app.automation.watch import WatchAgent, WatchReview, build_watch_prompt, judge
from app.automation.watchable import run_watch_tool
from app.models import Approval, AutomationOccurrence, Message, ScheduledJob, Step, Task, UsageLog, Watch
from app.models.enums import ApprovalStatus, MessageRole, TaskOrigin, TaskStatus, UsagePurpose
from app.push import PushKind

logger = logging.getLogger(__name__)


class AutomationLauncher(Protocol):
    """예약·감시 워크플로를 시작하는 객체(app/agent/workflows.py의 WorkflowLauncher).

    start_run·start_resume은 outbox.OutboxLauncher 몫이다(run_check가 밀린 outbox 요청을 다시 시작할 때 쓴다).
    테스트의 시작기(tests/conftest.py)는 워크플로가 끝날 때까지 기다린다. 메서드 이름·인자를 바꾸면
    WorkflowLauncher, outbox.OutboxLauncher, 테스트 시작기를 같이 고친다.
    """

    async def start_schedule(self, job_id: int, run_at: datetime, checked_at: datetime) -> Any: ...

    async def start_watch(self, watch_id: int, run_at: datetime, checked_at: datetime) -> Any: ...

    async def start_run(self, task_id: int, prompt: str) -> Any: ...

    async def start_resume(self, task_id: int, workflow_id: str) -> Any: ...


@dataclass(frozen=True)
class AutomationServices:
    """자동화 워크플로가 쓰는 묶음. workflows.automation_services_from_state가 app.state로 만든다(테스트도 같다)."""

    run: RunServices
    watch_agent: WatchAgent
    launcher: AutomationLauncher


@dataclass(frozen=True)
class CheckReport:
    """한 번의 검사에서 한 일(로그·테스트용)."""

    approvals_expired: int
    watches_expired: int
    schedules_started: int
    watches_started: int


@dataclass(frozen=True)
class DueWork:
    """find_due 단계 결과(DBOS에 기록). 모양을 바꾸면 WORKFLOW_VERSION을 올린다."""

    schedules: list[tuple[int, datetime]]  # (예약 id, 예정 시각)
    watches: list[tuple[int, datetime]]  # (감시 id, 예정 시각)


# ---- 1분 검사 ----


async def run_check(services: AutomationServices, now: datetime) -> CheckReport:
    """검사 한 번. 예약·감시 실행은 워크플로를 시작만 하고(테스트의 시작기는 끝날 때까지 기다린다) 결과를 모른다.

    now는 automation_check 워크플로의 check_clock 단계 값이다. 테스트는 여기에 원하는 시각을 직접 넣는다.
    부수 효과: 승인 만료·감시 끄기(각 단계에서 커밋), outbox 요청과 예약·감시 워크플로 시작, 매시 17분 보관 정리.
    중복 실행: 같은 분은 DBOS 워크플로 ID로 한 번만 돈다. 검사가 겹쳐도 시작하는 워크플로 ID가 예정 시각으로
    정해져 있어 예약·감시는 한 번만 실행된다. 단계를 더하거나 순서를 바꾸면 WORKFLOW_VERSION을 올린다.
    """
    factory = services.run.session_factory
    expired = await durable_step("expire_approvals", expire_overdue_approvals, factory, now)
    watches_expired = await durable_step("expire_watches", expire_watches, factory, now)
    # 3단계 보강: 시작하지 못한 outbox 요청(시도 시각이 된 것)을 다시 시작한다. 예약과 같이 목록은 단계로 기록하고
    # 시작은 워크플로 코드에서 한다(복구 때 같은 순서·같은 워크플로 ID). 겹쳐도 한 번만 돈다.
    pending = await durable_step("find_outbox", outbox.due_events, factory, now)
    await outbox.dispatch_events(services.launcher, factory, pending, now)
    due = await durable_step("find_due", find_due, factory, now)
    for job_id, run_at in due.schedules:
        await services.launcher.start_schedule(job_id, run_at, now)
    for watch_id, run_at in due.watches:
        await services.launcher.start_watch(watch_id, run_at, now)
    # 5단계 보강: 한 시간에 한 번 보관 기간이 지난 실행 기록을 정리한다(retention.py).
    if now.minute == retention.RETENTION_MINUTE:
        pruned = await retention.prune_expired_records(factory, services.run.settings, now)
        logger.info("보관 기간 정리: %s", pruned)
    report = CheckReport(expired, watches_expired, len(due.schedules), len(due.watches))
    if any((expired, watches_expired, due.schedules, due.watches)):
        logger.info("자동화 검사: %s", report)
    return report


async def overdue_task_ids(session: AsyncSession, now: datetime) -> list[int]:
    """만료 시각이 지난 pending 승인을 가진 승인 대기 task id들(잠그지 않는다. 잠근 뒤 상태는 다시 본다)."""
    rows = await session.scalars(
        select(Task.id)
        .join(Step, Step.task_id == Task.id)
        .join(Approval, Approval.step_id == Step.id)
        .where(
            Task.status == TaskStatus.WAITING_APPROVAL,
            Approval.status == ApprovalStatus.PENDING,
            Approval.expires_at <= now,
        )
        .distinct()
    )
    return list(rows)


async def expire_overdue_approvals(factory: async_sessionmaker[AsyncSession], now: datetime) -> int:
    """만료된 승인을 정리한다(2단계 expire_task 재사용). task를 잠그고 아직 승인 대기일 때만 바꾼다.

    결과: 실제로 바꾼 task 수. task마다 따로 커밋해 잠금을 짧게 쥔다.
    승인 API도 expires_at을 직접 검사하고 같은 task 행을 FOR UPDATE로 잠그므로, 사용자의 승인과 겹쳐도
    한쪽만 상태를 바꾼다(만료된 승인이 실행되는 사고를 막음). 푸시는 보내지 않는다(DECISIONS 2026-11-17).
    """
    expired = 0
    async with factory() as session:
        for task_id in await overdue_task_ids(session, now):
            task = await runner.lock_task(session, task_id)
            if task is not None and task.status == TaskStatus.WAITING_APPROVAL:
                await approvals.expire_task(session, task)
                expired += 1
            await session.commit()
    return expired


async def expire_watches(factory: async_sessionmaker[AsyncSession], now: datetime) -> int:
    """만료일이 지난 켜진 감시를 확인 차례가 아니어도 끈다(last_result.status=expired). 끈 수를 돌려주고 커밋한다.

    끄지 않으면 만료된 감시가 화면에 켜진 채로 보이고 활성 10개 칸(store.ensure_watch_allowed)을 계속 차지한다.
    """
    async with factory() as session:
        result = await session.execute(
            update(Watch)
            .where(Watch.is_active.is_(True), Watch.expires_at <= now)
            .values(is_active=False, last_result={"status": "expired", "checked_at": now.isoformat()})
            .returning(Watch.id)
        )
        count = len(result.all())
        await session.commit()
    return count


async def find_due(factory: async_sessionmaker[AsyncSession], now: datetime) -> DueWork:
    """차례가 된 켜진 예약·감시와 그 예정 시각(DB의 next_run_at 그대로). 읽기만 한다.

    예정 시각을 그대로 워크플로 ID에 넣으므로 검사가 여러 번 같은 행을 찾아도 같은 워크플로가 된다.
    서버가 오래 꺼져 있었어도 행마다 next_run_at이 하나라 행당 많아야 1건이다.
    """
    async with factory() as session:
        jobs = await session.execute(
            select(ScheduledJob.id, ScheduledJob.next_run_at)
            .where(ScheduledJob.is_active.is_(True), ScheduledJob.next_run_at <= now)
            .order_by(ScheduledJob.next_run_at, ScheduledJob.id)
        )
        watches = await session.execute(
            select(Watch.id, Watch.next_run_at)
            .where(Watch.is_active.is_(True), Watch.next_run_at <= now, Watch.expires_at > now)
            .order_by(Watch.next_run_at, Watch.id)
        )
        return DueWork([(i, t) for i, t in jobs.all()], [(i, t) for i, t in watches.all()])


# ---- 예약 작업 실행 ----

# 6단계 보강: automation_occurrences의 값. (kind, source_id, scheduled_for) 유일 조건이 같은 예정 시각의 두 번째
# 실행을 막는다. 값을 바꾸면 이미 쌓인 행(보관 7일)과 달라지고, docs/STATE_MACHINES.md 5절과 모델 설명
# (app/models/automation.py)도 고친다. 발생 기록은 API로 나가지 않는 기록용이다.
# 감시 last_result.status(paused_budget 등)와 이름이 달라 OCCURRENCE_SKIP_REASONS로 바꿔 적는다.
OCCURRENCE_SCHEDULE = "schedule"
OCCURRENCE_WATCH = "watch"
OCCURRENCE_STARTED = "started"
OCCURRENCE_SKIPPED = "skipped"
SKIP_MISSED = "missed"  # 유예 시간보다 오래 놓쳤다(서버가 꺼져 있었다)
OCCURRENCE_SKIP_REASONS = {"expired": "expired", "paused_budget": "budget"}


@dataclass(frozen=True)
class ScheduledRun:
    """claim_schedule 단계 결과(DBOS에 기록): 실행할 task, 프롬프트, 완료 푸시 종류."""

    task_id: int
    prompt: str
    done_kind: PushKind


async def lock_job(session: AsyncSession, job_id: int) -> ScheduledJob | None:
    """예약 행을 FOR UPDATE로 잠그고 최신 값으로 읽는다(세션 캐시 덮어씀). 지워졌으면 None.

    화면의 끄기·지우기와 claim이 같은 행을 잠가, 끈 뒤에 새 실행이 잡히지 않게 한다.
    user_id 조건이 없는 것은 1분 검사가 찾은 id만 넘어오기 때문이다(사용자 요청 경로에서 쓰지 않는다).
    """
    return await session.scalar(
        select(ScheduledJob)
        .where(ScheduledJob.id == job_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


def done_kind_for(job: ScheduledJob) -> PushKind:
    """완료 푸시 종류: 아침 브리핑(kind='briefing')은 briefing, 그 밖은 task_done(명세: 자동 작업은 완료 때 항상)."""
    return PushKind.BRIEFING if job.kind == rules.BRIEFING_KIND else PushKind.TASK_DONE


async def create_schedule_task(session: AsyncSession, job: ScheduledJob, run_at: datetime) -> Task:
    """'예약 작업' 대화방에 사용자 메시지 없는 task(origin=schedule, queued)를 만든다. 커밋은 claim이 한다.

    outbox를 거치지 않는다: 같은 run_schedule 워크플로가 바로 runner.run_task로 실행한다.
    last_task_id는 자동화 화면의 "마지막 실행 결과"가 읽는다.
    """
    conversation = await store.automation_conversation(session, job.user_id)
    task = Task(conversation_id=conversation.id, user_id=job.user_id, origin=TaskOrigin.SCHEDULE)
    session.add(task)
    await session.flush()
    job.last_run_at, job.last_task_id = run_at, task.id
    return task


async def find_occurrence(
    session: AsyncSession, kind: str, source_id: int, scheduled_for: datetime
) -> AutomationOccurrence | None:
    """이 예정 시각의 발생 기록. 행을 잠근 뒤 불러야 "없음"을 믿을 수 있다."""
    return await session.scalar(
        select(AutomationOccurrence).where(
            AutomationOccurrence.kind == kind,
            AutomationOccurrence.source_id == source_id,
            AutomationOccurrence.scheduled_for == scheduled_for,
        )
    )


def add_occurrence(
    session: AsyncSession, row: ScheduledJob | Watch, scheduled_for: datetime, *, skip: str | None, task_id: int | None
) -> None:
    """6단계 보강: 발생 한 건을 남긴다. 예약·감시 행을 잠근 트랜잭션 안에서 부르고, DB 유일 조건이 한 번 더 막는다."""
    session.add(
        AutomationOccurrence(
            user_id=row.user_id,
            kind=OCCURRENCE_SCHEDULE if isinstance(row, ScheduledJob) else OCCURRENCE_WATCH,
            source_id=row.id,
            scheduled_for=scheduled_for,
            status=OCCURRENCE_SKIPPED if skip else OCCURRENCE_STARTED,
            skip_reason=skip,
            task_id=task_id,
        )
    )


def replay_claim(job: ScheduledJob, occurrence: AutomationOccurrence) -> ScheduledRun | None:
    """이미 잡힌 발생(복구·중복 시작): 실행했던 것이면 같은 task, 건너뛴 것이면 None.

    claim 단계가 커밋한 뒤 DBOS가 결과를 기록하기 전에 죽으면 단계가 다시 돈다. 그때 task를 새로 만들지 않고
    같은 task를 돌려줘 예약 한 번에 task가 두 개 생기는 사고를 막는다. 그 task가 이미 끝났으면
    runner.run_task의 claim이 다시 실행하지 않는다.
    """
    if occurrence.status != OCCURRENCE_STARTED or occurrence.task_id is None:
        return None
    return ScheduledRun(occurrence.task_id, job.prompt_text, done_kind_for(job))


async def claim_schedule_run(
    factory: async_sessionmaker[AsyncSession], job_id: int, run_at: datetime, now: datetime
) -> ScheduledRun | None:
    """이 예정 시각의 실행을 한 번만 잡는다(automation_occurrences). 이미 잡았으면 같은 task를 돌려준다.

    유예 시간(2시간)보다 오래된 실행은 건너뛰고 이유(missed)를 남긴다. 다음 시각은 지금부터 다시 계산하므로
    서버가 며칠 꺼져 있었어도 밀린 실행이 몰리지 않는다(예약마다 next_run_at이 하나뿐이라 많아야 1건).

    결과: 실행할 ScheduledRun, 또는 None(지워짐·꺼짐·예정 시각이 이미 바뀜·건너뜀).
    부수 효과: 예약 행 잠금, next_run_at·last_run_at·last_task_id 갱신, task와 발생 기록 생성을 한 트랜잭션으로 커밋.
    중복 방지는 두 겹이다: 워크플로 ID(예정 시각)와 여기의 행 잠금 + "next_run_at = 예정 시각" + 발생 기록.
    이미 잡힌 실행은 그 뒤 끄거나 지워도 끝까지 돈다(다음 실행부터 반영, DECISIONS 2026-11-17 6단계 보강).
    """
    async with factory() as session:
        job = await lock_job(session, job_id)
        if job is None:
            return None
        existing = await find_occurrence(session, OCCURRENCE_SCHEDULE, job.id, run_at)
        if existing is not None:
            return replay_claim(job, existing)
        if not job.is_active or job.next_run_at != run_at:
            return None
        job.next_run_at = rules.next_schedule_run(job.time_of_day, job.weekdays, max(now, run_at), job.timezone)
        if now - run_at > rules.SCHEDULE_CATCH_UP:
            logger.info("예약 %s: %s 실행은 유예 시간이 지나 건너뜀", job_id, run_at.isoformat())
            add_occurrence(session, job, run_at, skip=SKIP_MISSED, task_id=None)
            await session.commit()
            return None
        task = await create_schedule_task(session, job, run_at)
        add_occurrence(session, job, run_at, skip=None, task_id=task.id)
        claimed = ScheduledRun(task.id, job.prompt_text, done_kind_for(job))
        await session.commit()
        return claimed


async def run_scheduled_job(services: RunServices, job_id: int, run_at: datetime, now: datetime) -> None:
    """run_schedule 워크플로 본문. claim 뒤 메인 에이전트 실행(runner.run_task)을 같은 워크플로에서 이어 한다.

    READ 도구만 주는 것(절대 원칙 7, toolsets의 origin별 조합)·이전 기록을 이어받지 않는 것·하루 비용 검사는
    runner가 task.origin=schedule을 보고 정한다. 여기서는 도구 목록을 고르지 않고, 완료 푸시 종류만 넘긴다.
    claim이 None이면 아무것도 하지 않는다(task 없음, 푸시 없음).
    """
    claimed = await durable_step("claim_schedule", claim_schedule_run, services.session_factory, job_id, run_at, now)
    if claimed is None:
        return
    await runner.run_task(services, claimed.task_id, prompt=claimed.prompt, done_kind=claimed.done_kind)


# ---- 감시 실행 ----


@dataclass(frozen=True)
class WatchJob:
    """claim_watch 단계 결과(DBOS에 기록). 감시 행에서 확인에 필요한 값만 복사한 것이다.

    필드를 바꾸면 WORKFLOW_VERSION을 올린다. 기본값이 있는 칸은 6단계 보강 전에 기록된 결과를 읽기 위한 것이다.
    """

    id: int
    user_id: int
    description: str
    tool_name: str
    tool_input: dict[str, Any]
    condition_text: str
    condition_type: str = conditions.TEXT
    condition_spec: dict[str, Any] | None = None


async def lock_watch(session: AsyncSession, watch_id: int) -> Watch | None:
    """감시 행을 FOR UPDATE로 잠그고 최신 값으로 읽는다. 지워졌으면 None(lock_job과 같은 이유)."""
    return await session.scalar(
        select(Watch).where(Watch.id == watch_id).with_for_update().execution_options(populate_existing=True)
    )


async def claim_watch_run(services: RunServices, watch_id: int, run_at: datetime, now: datetime) -> WatchJob | None:
    """이 예정 시각의 확인을 한 번만 잡는다.

    다음 확인을 먼저 now + 간격으로 미뤄 둔다(도중에 죽어도 다시 몰리지 않게).

    결과: 확인할 WatchJob, 또는 None(지워짐·꺼짐·예정 시각이 바뀜·이미 잡힘·만료·하루 비용 상한).
    만료면 감시를 끄고, 비용 상한이면 도구·모델을 부르지 않고 last_result.status=paused_budget을 남긴다
    (화면의 "오늘은 쉬는 중"). 건너뛴 것도 발생 기록(skipped)을 남겨 같은 예정 시각을 다시 잡지 않는다.
    부수 효과: 감시 행 잠금, next_run_at·is_active·last_result, 발생 기록을 한 트랜잭션으로 커밋.
    """
    async with services.session_factory() as session:
        watch = await lock_watch(session, watch_id)
        if watch is None or not watch.is_active or watch.next_run_at != run_at:
            return None
        if await find_occurrence(session, OCCURRENCE_WATCH, watch.id, run_at) is not None:
            return None
        watch.next_run_at = now + timedelta(minutes=watch.interval_minutes)
        skip = None
        if watch.expires_at <= now:
            watch.is_active, skip = False, "expired"
        elif await daily_cost_exceeded(session, watch.user_id, services.settings):
            skip = "paused_budget"  # 6단계 보강: 화면에 "오늘은 쉬는 중"으로 보인다
        add_occurrence(session, watch, run_at, skip=OCCURRENCE_SKIP_REASONS.get(skip or ""), task_id=None)
        if skip is not None:
            watch.last_result = {"status": skip, "checked_at": now.isoformat()}
            await session.commit()
            return None
        job = WatchJob(
            watch.id,
            watch.user_id,
            watch.description,
            watch.tool_name,
            watch.tool_input,
            watch.condition_text,
            watch.condition_type,
            watch.condition_spec,
        )
        await session.commit()
        return job


async def execute_watch_tool(services: RunServices, job: WatchJob) -> ToolResult:
    """저장된 도구를 실행한다(watch_tool 단계). 예외를 내지 않고 실패는 ok=false ToolResult다.

    도구가 READ이고 메일이 아닌지는 run_watch_tool이 실행 때 다시 검사한다(절대 원칙 7. DB 값이 바뀌어 있어도
    INTERNAL·EXTERNAL 도구가 돌지 않게). 메인 Agent run이 아니라 StepTracking·데이터 정책 검사는 거치지 않는다.
    """
    deps = AgentDeps(
        user_id=job.user_id,
        task_id=0,  # 감시 확인은 task를 만들지 않는다. READ 도구는 task_id를 쓰지 않는다.
        origin=TaskOrigin.WATCH,
        db=services.session_factory,
        settings=services.settings,
        http=services.http,
    )
    return await run_watch_tool(deps, job.tool_name, job.tool_input)


async def judge_watch(
    services: RunServices, agent: WatchAgent, job: WatchJob, result: ToolResult, now: datetime
) -> WatchReview:
    """문장 조건을 판정 에이전트로 판정한다(watch_verdict 단계, 모델 호출·비용 발생).

    예산은 task 없이 "watch:{감시 id}:{시각}" 키로 하루 원장에 예약·정산한다(BudgetCapability). 단계라서 복구 때
    모델을 다시 부르지 않는다. 실패는 예외 대신 judged=False(watch.judge)라 unknown으로 기록된다.
    """
    prompt = build_watch_prompt(
        description=job.description, condition_text=job.condition_text, checked_at=now, result=result
    )
    scope = BudgetScope(
        user_id=job.user_id,
        task_id=None,
        key_prefix=f"watch:{job.id}:{now.isoformat()}",
        purpose=UsagePurpose.WATCH,
        db=services.session_factory,
        settings=services.settings,
    )
    return await judge(agent, prompt, scope)


def verdict_for(job: WatchJob, result: ToolResult, review: WatchReview | None) -> conditions.Verdict:
    """6단계 보강: 숫자 조건은 서버 비교, 문장 조건은 판정 모델. 조회·판정 실패는 unknown(조건 아님이 아니다)."""
    if job.condition_type == conditions.NUMBER and job.condition_spec:
        verdict = conditions.judge_number(job.condition_spec, result)
        if job.tool_name == "coupang_price" and result.ok:
            content = conditions.payload_of(result)
            if isinstance(content, dict) and isinstance(content.get("summary"), str):
                return conditions.Verdict(verdict.status, verdict.reason, content["summary"])
        return verdict
    if not result.ok or review is None:
        return conditions.Verdict(conditions.UNKNOWN, conditions.TOOL_FAILED, "")
    if not review.judged:
        return conditions.Verdict(conditions.UNKNOWN, conditions.JUDGE_FAILED, "")
    return conditions.Verdict(conditions.MET if review.met else conditions.NOT_MET, None, review.summary)


def last_result(verdict: conditions.Verdict, result: ToolResult, now: datetime) -> dict[str, Any]:
    """watches.last_result에 넣을 값. 키를 바꾸면 schemas/automations.py의 WatchResultOut과 화면을 같이 고친다."""
    return {
        "status": verdict.status,
        "reason": verdict.reason,
        "checked_at": now.isoformat(),
        "summary": verdict.summary,
        "error": result.error,
        "sources": result.sources,
    }


def should_notify(watch: Watch, verdict: conditions.Verdict, now: datetime) -> bool:
    """거짓(또는 처음)→참으로 바뀌었고, 마지막 알림에서 12시간 이상 지났을 때만 알린다.

    계속 참이면 다시 알리지 않는다. 12시간 안에 다시 참이 되면 그 전환은 알리지 않고 넘어간다.
    unknown(조회·판정 실패)은 알리지 않는다. 참인 조건이 간격마다 푸시를 다시 보내지 않게 하는 규칙이다
    (DECISIONS 2026-11-17 6단계 보강 "다시 알림 정책"). 12시간은 rules.WATCH_REPEAT_DELAY.
    """
    if verdict.status != conditions.MET or watch.last_known_met is True:
        return False
    return watch.last_notified_at is None or now - watch.last_notified_at >= rules.WATCH_REPEAT_DELAY


def met_message(watch: Watch, summary: str, result: ToolResult, now: datetime) -> str:
    """'예약 작업' 대화방에 남길 충족 알림 본문. summary는 판정 모델의 글(숫자 조건이면 서버의 비교 문장)이다.

    출처는 본문 줄과 별도로 Message.sources에도 저장한다(notify_met). 화면은 그 목록으로 링크를 그린다.
    """
    lines = [f"[조건 감시] {watch.description}", f"조건: {watch.condition_text}", summary]
    if result.sources:
        lines.append("출처: " + " ".join(result.sources))
    lines.append(f"확인 시각: {rules.seoul_text(now)}")
    if not watch.repeat:
        lines.append("한 번 알렸으므로 이 감시는 꺼졌습니다.")
    return "\n".join(line for line in lines if line)


async def record_watch_usage(session: AsyncSession, user_id: int, review: WatchReview | None) -> None:
    """판정 비용을 usage_logs(purpose=watch, task 없음)에 더한다. 커밋은 finish_watch가 한다.

    판정하지 않았거나(숫자 조건·도구 실패) 사용량을 모르면 남기지 않는다(0원으로 치지 않는다).
    """
    if review is None or review.usage is None:
        return
    usage = review.usage
    session.add(
        UsageLog(
            user_id=user_id,
            task_id=None,
            purpose=UsagePurpose.WATCH,
            model=review.model[:100],
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cost_usd=calculate_cost(review.model, usage.input_tokens, usage.output_tokens),
        )
    )


async def notify_met(session: AsyncSession, watch: Watch, summary: str, result: ToolResult, now: datetime) -> int:
    """대화방에 요약과 출처를 남긴다. repeat=false면 감시를 끈다. 알릴 대화방 id를 돌려준다."""
    conversation = await store.automation_conversation(session, watch.user_id)
    session.add(
        Message(
            conversation_id=conversation.id,
            role=MessageRole.ASSISTANT,
            content=met_message(watch, summary, result, now),
            # 8단계: 화면이 본문을 파싱하지 않고 출처 링크를 그릴 수 있게 따로 저장한다.
            sources=list(result.sources),
        )
    )
    conversation.updated_at = func.now()
    watch.last_notified_at = now
    if not watch.repeat:
        watch.is_active = False
    return conversation.id


async def finish_watch(
    factory: async_sessionmaker[AsyncSession],
    job: WatchJob,
    result: ToolResult,
    review: WatchReview | None,
    now: datetime,
) -> int | None:
    """결과를 저장한다. 알려야 하면 대화방 id, 아니면 None. 그 사이 꺼지거나 지워진 감시는 알리지 않는다.

    판정 도중 사용자가 끄거나 지웠어도 이미 쓴 비용(usage_logs)은 남긴다. unknown이면 last_known_met을 바꾸지 않아
    실패 한 번이 "거짓→참" 전환으로 잘못 세어지지 않게 한다. 감시 행 잠금 + 한 트랜잭션 커밋(finish_watch 단계).
    """
    verdict = verdict_for(job, result, review)
    async with factory() as session:
        await record_watch_usage(session, job.user_id, review)
        watch = await lock_watch(session, job.id)
        conversation_id = None
        if watch is not None and watch.is_active:
            watch.last_result = last_result(verdict, result, now)
            if should_notify(watch, verdict, now):
                conversation_id = await notify_met(session, watch, verdict.summary, result, now)
            if verdict.status != conditions.UNKNOWN:
                watch.last_known_met = verdict.status == conditions.MET
        await session.commit()
    return conversation_id


async def send_watch_push(services: RunServices, user_id: int, conversation_id: int, key: str) -> int:
    """watch_met 푸시(본문은 고정 문구, app/push.py). key가 같으면 한 번만 보내진다(notify_watch 단계)."""
    return await services.notifier.notify(user_id, PushKind.WATCH_MET, f"/chat/{conversation_id}", key)


async def run_watch(services: AutomationServices, watch_id: int, run_at: datetime, now: datetime) -> None:
    """run_watch 워크플로 본문(task를 만들지 않는다). 단계: claim_watch → watch_tool → watch_verdict → finish_watch
    → notify_watch. 단계 이름·순서를 바꾸면 WORKFLOW_VERSION을 올린다.

    도구가 실패하면 판정 모델을 부르지 않는다(비용 없음, unknown). 숫자 조건도 모델 없이 서버가 비교한다.
    """
    run = services.run
    job = await durable_step("claim_watch", claim_watch_run, run, watch_id, run_at, now)
    if job is None:
        return
    result = await durable_step("watch_tool", execute_watch_tool, run, job)
    review = None
    if result.ok and job.condition_type != conditions.NUMBER:  # 숫자 조건은 모델을 부르지 않는다
        review = await durable_step("watch_verdict", judge_watch, run, services.watch_agent, job, result, now)
    conversation_id = await durable_step("finish_watch", finish_watch, run.session_factory, job, result, review, now)
    if conversation_id is not None:
        key = f"watch_met:watch-{watch_id}:{run_at.isoformat()}"  # 같은 감시·같은 예정 시각의 알림은 하나
        await durable_step("notify_watch", send_watch_push, run, job.user_id, conversation_id, key)
