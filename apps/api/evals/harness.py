"""eval 실행기: 사례 하나를 앱과 같은 실행 흐름(runner.run_task)으로 돌리고 결과(EvalOutput)를 모은다.

외부에 흔적을 남기지 않는 장치
- DB: eval 전용 DB(이름이 _eval로 끝나야 함, run_evals.py가 만들고 끝나면 지운다)에 사례마다 새 사용자를 만든다.
  개발 DB에는 마지막에 usage_logs(purpose=eval) 합계만 남긴다(record_eval_usage).
- 외부 API: 설정의 키를 전부 가짜로 바꾸고(eval_settings), HTTP 클라이언트는 가짜 응답만 주는 MockTransport다
  (evals/fake_externals.py). 실제 카카오·네이버·Open-Meteo·Google로 나가는 요청이 없다.
- 승인: EXTERNAL 도구는 승인 대기(waiting_approval)에서 멈춘다. 이 실행기는 승인하지 않는다(승인 API도,
  승인 결정 함수도 부르지 않는다). 승인 요청이 만들어졌는지만 본다.
  예외 하나(2026-11-18): 사례에 revision_note가 있으면 첫 대기 카드에 '고쳐서 다시'를 보낸다
  (approvals.request_revision — 거절만 할 수 있는 함수) → runner.resume_task로 이어서 실행한다.
  고친 뒤의 새 카드도 승인하지 않고 대기에서 멈춘다.
- 푸시: VAPID 설정을 비워 보내지 않는다. 관찰: TELEMETRY_ENABLED=false, 토큰도 비워 보내지 않는다.
- DBOS: 쓰지 않는다. runner.run_task를 워크플로 밖에서 부르면 단계가 그냥 실행된다(app/agent/durable.py).

고정된 세상(8단계 보강): 사례마다 app.clock을 사례의 now로 바꿔 끼운다(frozen_clock). 사례는 하나씩 차례로 돈다
(max_concurrency=1). 계정은 모두 Google 연결(캘린더 + 메일 읽기 권한)과 메일 데이터 처리 동의가 된 상태다.

흐름 속 위치: run_evals.evaluate가 build_environment → case_task를 Dataset.evaluate에 넘긴다. Pydantic Evals가 사례마다
task(inputs)를 부르면 run_case가 돌고, 돌려준 EvalOutput을 evaluators가 판정한다. 끝난 뒤 run_evals.record_usage가
record_eval_usage로 개발 DB에 비용을 남긴다.

근거: BUILD_SPEC 16절(eval), DECISIONS 2026-11-17(버리는 DB, 가짜 외부 API, 승인하지 않음, usage_logs purpose=eval,
app.clock 고정), 2026-11-17(eval 설정에 실행마다 새 PAYLOAD_ENCRYPTION_KEY).

고칠 때 주의
- 승인을 결정하는 코드를 이 디렉터리에 넣지 않는다. tests/test_evals.py의 '실행기는 승인하지 않음' 소스 검사 테스트가
  evals/*.py 전체(주석 포함)에서 승인 결정 함수·승인 결과 이름을 찾는다. 승인을 영어 낱말로 적지 않는다.
- runner.run_task의 인자나 RunServices 모양이 바뀌면 run_case를 같이 고친다(실제 앱 흐름과 같아야 eval이 의미 있다).
- Settings에 외부 API 키·푸시·관찰 설정을 새로 더하면 eval_settings에서 가짜 값이나 빈 값으로 덮는다.
  덮지 않으면 .env의 실제 키가 eval에서 쓰인다.
- 사례 준비 모양(사용자·Google 연결·메일 동의)을 바꾸면 dataset.yaml 머리와 docs/EVALS.md '고정된 세상'도 고친다.

변경 이력
- 2026-11-17 처음 작성(8단계).
- 2026-11-17 7단계 보강: eval 설정에 새 PAYLOAD_ENCRYPTION_KEY(메일 도구가 보이게), 메일 데이터 처리 동의.
- 2026-11-17 8단계 보강: 사례마다 app.clock 고정(frozen_clock), 모델이 시도한 도구 모으기(attempted_tools).
- 2026-11-18 주석 보강(동작 변경 없음).
- 2026-11-18 '고쳐서 다시' 사례(extra_dataset.yaml): send_revision, tool_calls의 revised 표시.
"""

import secrets
from collections.abc import Awaitable, Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from unittest.mock import patch

from cryptography.fernet import Fernet
from pydantic import SecretStr
from pydantic_ai.messages import ModelResponse, ToolCallPart
from sqlalchemy import func, make_url, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import clock
from app.agent.agent import MyAgent, build_agent
from app.agent.approvals import request_revision
from app.agent.guard import GuardAgent, build_guard_agent
from app.agent.history import load_messages
from app.agent.runner import RunServices, resume_task, run_task
from app.config import Settings
from app.google_auth import CALENDAR_SCOPE, GMAIL_SCOPE, GoogleTokens, save_tokens
from app.models import Approval, Conversation, Memory, Message, Step, Task, Todo, UsageLog, User
from app.models.enums import ApprovalStatus, MessageRole, TaskOrigin, UsagePurpose
from app.push import PushNotifier
from app.security import hash_password, token_cipher
from evals.cases import EvalInputs, EvalOutput, ModelUsage, ToolCall
from evals.fake_externals import EvalHttp

# 외부 API 키 자리에 넣는 가짜 값. 혹시 실제 서비스로 나가도 인증에 실패하게 하는 두 번째 안전장치다.
FAKE = "eval-fake-not-a-real-key"


@dataclass(frozen=True)
class EvalEnvironment:
    """한 번의 eval 실행 동안 모든 사례가 함께 쓰는 것: eval DB 세션, eval 설정, 메인·감시 에이전트."""

    session_factory: async_sessionmaker[AsyncSession]
    settings: Settings
    agent: MyAgent
    guard: GuardAgent


def check_eval_database_name(url: str) -> str:
    """eval DB 이름은 _eval로 끝나야 한다(개발 DB를 실수로 쓰거나 지우지 않게)."""
    name = make_url(url).database or ""
    if not name.endswith("_eval"):
        # 막는 사고: 개발 DB 주소를 그대로 넘겨 DROP DATABASE가 개발 DB를 지우는 것.
        raise ValueError(f"eval DB 이름은 _eval로 끝나야 합니다: {name}")
    return name


def eval_settings(base: Settings, database_url: str) -> Settings:
    """모델 키·모델 이름·비용 상한만 그대로 두고, 외부 API 키는 가짜로, 푸시·Logfire는 끈다.

    base를 바꾸지 않고 복사본을 돌려준다. token_encryption_key도 새로 만든다(가짜 Google 토큰을 버리는 DB에만 암호화).
    불변 조건: 실제 카카오·네이버·Google 키, VAPID 키, Logfire 토큰이 eval 설정에 남지 않는다.
    """
    return base.model_copy(
        update={
            "database_url": database_url,
            "kakao_rest_api_key": SecretStr(FAKE),
            "naver_client_id": FAKE,
            "naver_client_secret": SecretStr(FAKE),
            "tmap_app_key": SecretStr(FAKE),
            "google_client_id": FAKE,
            "google_client_secret": SecretStr(FAKE),
            "google_redirect_uri": "http://127.0.0.1/eval/callback",
            "token_encryption_key": SecretStr(Fernet.generate_key().decode("ascii")),
            # 7단계 보강: 메일 기능 플래그. 버리는 DB라 실행마다 새 키로 메일 도구를 모델에게 그대로 보인다.
            "payload_encryption_key": SecretStr(Fernet.generate_key().decode("ascii")),
            "vapid_public_key": "",
            "vapid_private_key": SecretStr(""),
            "vapid_subject": "",
            "telemetry_enabled": False,
            "logfire_token": SecretStr(""),
        }
    )


@contextmanager
def frozen_clock(moment: datetime) -> Iterator[None]:
    """사례의 now로 app.clock을 고정한다(날짜 지시문, 날씨 예보 범위, 자동화 시각, 조회 시각).

    모듈 함수 하나를 바꿔 끼우므로 같은 프로세스의 다른 사례와 동시에 돌면 안 된다(max_concurrency=1).
    승인 만료·예산 하루 경계·토큰 만료는 clock을 쓰지 않아 실제 시각 그대로다(DECISIONS 2026-11-17).
    """
    fixed = moment.astimezone(UTC)
    with patch.object(clock, "now_utc", lambda: fixed):
        yield


def build_environment(settings: Settings, session_factory: async_sessionmaker[AsyncSession]) -> EvalEnvironment:
    """앱과 같은 build_agent·build_guard_agent로 에이전트를 만든다(eval 전용 프롬프트·도구를 두지 않는다).

    settings는 eval_settings를 거친 것이어야 한다. 테스트는 만든 뒤 agent·guard를 override로 가짜 모델로 바꿔 낀다.
    """
    return EvalEnvironment(session_factory, settings, build_agent(settings), build_guard_agent(settings))


# ---- 사례 준비: 사용자, Google 연결(가짜 토큰), 미리 있던 할 일·기억, 대화와 task ----


async def create_case_user(session: AsyncSession, settings: Settings, now: datetime) -> User:
    """새 사용자: Google 연결(캘린더 + 메일 읽기, 가짜 토큰)과 메일 데이터 처리 동의가 된 상태.

    부수 효과: eval DB에 사용자·토큰 행을 쓴다(flush만, 커밋은 prepare_case). 이메일은 .invalid 도메인의 무작위 값이라
    실제 주소와 겹치지 않는다. 사례마다 새 사용자라 이전 사례의 기억·할 일·비용이 섞이지 않는다.
    """
    user = User(
        email=f"eval-{secrets.token_hex(6)}@eval.invalid",
        password_hash=hash_password(secrets.token_hex(16)),
        mail_consent_at=now,
    )
    session.add(user)
    await session.flush()
    cipher = token_cipher(settings)
    assert cipher is not None
    # 토큰 만료는 실제 시각 기준이다(토큰 갱신 검사는 app.clock을 쓰지 않는다).
    expires = datetime.now(UTC) + timedelta(days=1)
    tokens = GoogleTokens("eval-fake-access", None, expires, [CALENDAR_SCOPE, GMAIL_SCOPE])
    await save_tokens(session, user.id, tokens, cipher)
    return user


async def seed_case(session: AsyncSession, user: User, inputs: EvalInputs) -> None:
    """사례 시작 전 할 일·기억을 넣는다(커밋하지 않음).

    기억은 출처 메시지가 있어야 하므로(절대 원칙 9: 실제 출처에서만) '예전 대화'에 사용자가 "…. 기억해"라고 한
    메시지를 먼저 만들고 그 id를 source_message_id로 단다.
    """
    session.add_all(Todo(user_id=user.id, title=title) for title in inputs.seed_todos)
    if not inputs.seed_memories:
        return
    earlier = Conversation(user_id=user.id, title="예전 대화")
    session.add(earlier)
    await session.flush()
    for content in inputs.seed_memories:
        said = Message(conversation_id=earlier.id, role=MessageRole.USER, content=f"{content}. 기억해")
        session.add(said)
        await session.flush()
        session.add(Memory(user_id=user.id, content=content, source_message_id=said.id))


async def prepare_case(env: EvalEnvironment, inputs: EvalInputs) -> int:
    """사례 하나의 채팅 task를 만들고 id를 돌려준다(API의 메시지 전송과 같은 모양).

    부수 효과: eval DB에 사용자·대화·메시지·task를 한 트랜잭션으로 쓰고 커밋한다. outbox·DBOS 시작은 하지 않는다
    (run_case가 runner.run_task를 직접 부른다).
    """
    async with env.session_factory() as session:
        user = await create_case_user(session, env.settings, inputs.now)
        await seed_case(session, user, inputs)
        conversation = Conversation(user_id=user.id, title="eval")
        session.add(conversation)
        await session.flush()
        message = Message(conversation_id=conversation.id, role=MessageRole.USER, content=inputs.message)
        session.add(message)
        await session.flush()
        task = Task(
            conversation_id=conversation.id, user_id=user.id, trigger_message_id=message.id, origin=TaskOrigin.CHAT
        )
        session.add(task)
        await session.flush()
        message.task_id = task.id
        await session.commit()
        return task.id


# ---- 결과 모으기 ----


async def usage_of(session: AsyncSession, task_id: int) -> list[ModelUsage]:
    """그 task의 usage_logs를 용도(agent·guard)·모델별로 합친다. 실제 모델 호출이 남긴 비용이 그대로 들어온다."""
    rows = await session.execute(
        select(
            UsageLog.purpose,
            UsageLog.model,
            func.sum(UsageLog.input_tokens),
            func.sum(UsageLog.output_tokens),
            func.sum(UsageLog.cost_usd),
            func.count(),
        )
        .where(UsageLog.task_id == task_id)
        .group_by(UsageLog.purpose, UsageLog.model)
        .order_by(UsageLog.purpose, UsageLog.model)
    )
    return [
        ModelUsage(
            purpose=purpose.value, model=model, input_tokens=int(i), output_tokens=int(o), cost_usd=float(c), calls=n
        )
        for purpose, model, i, o, c, n in rows
    ]


def attempted_tools(history: list[Any] | None) -> list[str]:
    """모델 기록에서 모델이 부르려 한 도구 이름 전부(보이지 않던 도구·실행 전에 막힌 호출 포함)."""
    messages = load_messages(history or [])
    return [
        part.tool_name
        for message in messages
        if isinstance(message, ModelResponse)
        for part in message.parts
        if isinstance(part, ToolCallPart)
    ]


async def tool_calls(session: AsyncSession, task_id: int) -> list[ToolCall]:
    """steps 행마다 하나. 승인 요청이 만들어졌는지도 함께(상태는 pending 그대로다. 이 실행기는 결정하지 않는다).

    args는 steps.input이다(ToolArguments·ApprovalScope가 이 값으로 인자·대상을 본다).
    """
    steps = list(await session.scalars(select(Step).where(Step.task_id == task_id).order_by(Step.order)))
    rows = await session.execute(
        select(Approval.step_id, Approval.revision_note)
        .join(Step, Approval.step_id == Step.id)
        .where(Step.task_id == task_id)
    )
    # SQLAlchemy 2.1에서 Result.tuples()가 폐기 예정이라 Row를 바로 푼다(2026-11-19, 경고 21건 정리).
    notes = {step_id: note for step_id, note in rows.all()}
    return [
        ToolCall(
            name=step.tool_name,
            args=dict(step.input or {}),
            status=step.status.value,
            ok=step.ok,
            approval_requested=step.id in notes,
            revised=notes.get(step.id) is not None,
        )
        for step in steps
    ]


async def collect_output(env: EvalEnvironment, task_id: int, http: EvalHttp) -> EvalOutput:
    """eval DB의 task·steps·usage_logs와 가짜 외부 API의 기록으로 평가기가 볼 결과를 만든다(읽기만)."""
    async with env.session_factory() as session:
        task = await session.get_one(Task, task_id, populate_existing=True)
        return EvalOutput(
            task_status=task.status.value,
            calls=await tool_calls(session, task_id),
            attempted_tools=attempted_tools(task.message_history),
            final_answer=task.final_answer,
            external_writes=list(http.writes),
            outbound_requests=list(http.requests),
            served_urls=list(http.served_urls),
            usage=await usage_of(session, task_id),
        )


async def run_case(env: EvalEnvironment, inputs: EvalInputs) -> EvalOutput:
    """사례 하나: 시각 고정 → 준비 → runner.run_task(실제 앱 흐름, 승인 대기에서 멈춤) → 결과.

    불변 조건: 승인하지 않는다(승인 대기면 run_task가 거기서 끝나고 그대로 결과를 모은다). 외부 HTTP는 EvalHttp뿐이다.
    실제 모델을 부르므로 비용이 든다(usage_logs는 eval DB에 남고, 개발 DB에는 record_eval_usage가 합계만 옮긴다).
    결과 모으기는 시각 고정 밖에서 한다(읽기뿐이라 시각과 무관).
    """
    http = EvalHttp(world=inputs)
    with frozen_clock(inputs.now):
        task_id = await prepare_case(env, inputs)
        async with http.client() as client:
            notifier = PushNotifier(env.session_factory, env.settings)  # VAPID가 비어 있어 아무것도 보내지 않는다
            services = RunServices(env.agent, env.guard, env.session_factory, env.settings, notifier, client)
            await run_task(services, task_id, prompt=inputs.message)
            if inputs.revision_note is not None and await send_revision(env, task_id, inputs.revision_note):
                await resume_task(services, task_id)
    return await collect_output(env, task_id, http)


# '고쳐서 다시' 사례만 쓴다. 첫 대기 카드(가장 앞 step)에 고칠 내용을 보낸다. 대기 카드가 없으면(모델이 되묻기로 끝냄)
# 보내지 않고 False → 이어서 실행하지 않는다(그 결과는 평가기가 경로로 판정한다).
# request_revision은 거절만 할 수 있다(승인으로 바꾸는 길이 없다). 커밋은 그 함수 안에서 한다.
async def send_revision(env: EvalEnvironment, task_id: int, note: str) -> bool:
    """첫 대기 카드에 '고쳐서 다시'를 보냈으면 True."""
    async with env.session_factory() as session:
        row = (
            await session.execute(
                select(Approval.id, Task.user_id)
                .join(Step, Approval.step_id == Step.id)
                .join(Task, Step.task_id == Task.id)
                .where(Task.id == task_id, Approval.status == ApprovalStatus.PENDING)
                .order_by(Step.order)
                .limit(1)
            )
        ).first()
        if row is None:
            return False
        card_id, user_id = row
        decision = await request_revision(session, card_id, user_id, note)
        return decision.all_decided


def case_task(env: EvalEnvironment) -> Callable[[EvalInputs], Awaitable[EvalOutput]]:
    """Dataset.evaluate에 넘길 task 함수."""

    async def task(inputs: EvalInputs) -> EvalOutput:
        return await run_case(env, inputs)

    return task


# ---- 비용 기록(개발 DB) ----


def eval_usage_rows(user_id: int, outputs: Sequence[EvalOutput]) -> list[UsageLog]:
    """사례·모델마다 한 행(purpose=eval, task 없음). 메인·감시 에이전트 사용량을 합친다.

    task 없음: 사례의 task는 지워질 eval DB에만 있다. user_id는 --email 계정이라 그 계정의 하루 비용에 들어간다
    (DECISIONS 2026-11-16 usage_logs.user_id, 2026-11-17 eval 비용). 모델 이름은 칸 길이(100자)에 맞춰 자른다.
    """
    rows: list[UsageLog] = []
    for output in outputs:
        by_model: dict[str, tuple[int, int, Decimal]] = {}
        for usage in output.usage:
            inp, out, cost = by_model.get(usage.model, (0, 0, Decimal(0)))
            cost += Decimal(str(usage.cost_usd))
            by_model[usage.model] = (inp + usage.input_tokens, out + usage.output_tokens, cost)
        rows.extend(
            UsageLog(
                user_id=user_id,
                task_id=None,
                purpose=UsagePurpose.EVAL,
                model=model[:100],
                input_tokens=inp,
                output_tokens=out,
                cost_usd=cost,
            )
            for model, (inp, out, cost) in by_model.items()
        )
    return rows


async def record_eval_usage(session: AsyncSession, user_id: int, outputs: Sequence[EvalOutput]) -> int:
    """개발 DB usage_logs에 eval 비용을 쓰고 커밋한다. 돌려주는 값은 쓴 행 수(실행 명령이 보여 준다)."""
    rows = eval_usage_rows(user_id, outputs)
    session.add_all(rows)
    await session.commit()
    return len(rows)
