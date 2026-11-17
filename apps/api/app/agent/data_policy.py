"""데이터 전송 등급과 도구 정책(4단계 보강, BUILD_SPEC 2.2·17절). 행동 위험 등급(READ/INTERNAL/EXTERNAL)과 따로 본다.

등급: public(일반 검색어·공개 정보) / user_private(일정·할 일·사용자 글) / mail_private(메일 본문·메타데이터)
/ secret(토큰·키·세션 비밀). secret은 어떤 도구에도 넣지 않는다(모델 입력에도 없다).

도구마다 TOOL_POLICIES에 적는다: 보내는 곳(service, 앱 안이면 None), 받을 수 있는 데이터 등급(accepts),
결과 등급(produces), 타임아웃, 재시도, 중복 방지, 결과 보관, 화면 표시. 새 도구는 여기에 등록해야 한다
(tests/test_data_policy.py가 빠진 도구를 잡는다).

도구 목록 거르기(visible_tool, 7단계 보강): 메일 기능 플래그가 꺼져 있으면 메일 도구를, 문맥에 읽어 온 메일이
있으면 공개 검색·장소·날씨 도구와 자동화 생성 도구(create_schedule·create_watch)를 모델에게 보이지 않는다
(runner가 toolsets.run_toolsets로 건다).

실행 전 검사(refusal_before_run, StepTracking이 도구 실행 직전에 부른다):
- 자동화 생성 도구는 메일을 읽은 문맥(이전 기록 포함)이거나 이번 작업에서 메일을 읽었으면 실행하지 않는다
  (automation_refusal, failed(data_policy)와 "새 대화에서 직접 요청" 안내).
- public만 받는 외부 도구(공개 검색·장소·날씨)는, 같은 작업에서 메일을 읽었으면 실행하지 않는다
  (메일 내용을 검색어로 외부에 보내는 행동을 자동 허용하지 않음).
- 같은 작업에서 조회한 일정 제목(2자 이상)이 인자에 들어 있으면 실행하지 않는다.
- 막으면 failed(data_policy)와 "공개 검색어를 직접 입력해 달라"는 안내. 모델 문장으로 허용하지 않는다.
일정 쓰기(EXTERNAL)는 사용자 본인의 Google 계정이고 승인을 거치므로 user_private·mail_private을 받는다.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.messages import ModelMessage, ModelRequest, ToolReturnPart
from pydantic_ai.tools import ToolDefinition
from sqlalchemy import select

from app.agent.deps import AgentDeps
from app.agent.results import ToolResult
from app.config import Settings
from app.models import Step
from app.models.enums import StepStatus
from app.security import payload_cipher


def mail_enabled(settings: Settings) -> bool:
    """메일 기능 플래그(7단계 보강). 복구 payload를 암호화할 키(PAYLOAD_ENCRYPTION_KEY)가 있을 때만 켠다.

    키가 없으면 DBOS 기록과 모델 기록에 메일 원문이 평문으로 남을 수 있어 메일 도구를 모델에게 주지 않고,
    도구도 Gmail을 부르지 않으며, 메일 권한 추가 시작도 막는다(BUILD_SPEC 5.2·13절 7단계).
    """
    return payload_cipher(settings) is not None


class DataClass(StrEnum):
    PUBLIC = "public"
    USER_PRIVATE = "user_private"
    MAIL_PRIVATE = "mail_private"
    SECRET = "secret"


@dataclass(frozen=True)
class ToolPolicy:
    service: str | None  # 데이터를 보내는 외부 서비스. 앱 안에서만 바뀌면 None
    accepts: frozenset[DataClass]  # 인자로 받아 보낼 수 있는 데이터 등급
    produces: DataClass  # 결과에 담기는 데이터 등급
    timeout_seconds: float
    retry: str  # 재시도 조건
    dedupe: str  # 중복 방지 방식
    retention: str  # 결과 보관
    display: str  # 사용자 표시


PUBLIC_ONLY = frozenset({DataClass.PUBLIC})
USER = frozenset({DataClass.PUBLIC, DataClass.USER_PRIVATE})
OWN_ACCOUNT = frozenset({DataClass.PUBLIC, DataClass.USER_PRIVATE, DataClass.MAIL_PRIVATE})
HTTP_TIMEOUT = 10.0  # app/tools/http.py
DB_TIMEOUT = 5.0
READ_RETRY = "조회 실패(시간 초과·연결·5xx·429)는 1회 재시도(app/tools/http.py)"
NO_RETRY = "자동 재시도 없음"
STEPS = "steps.output(작업 기록)"


def internal(display: str) -> ToolPolicy:
    """앱 DB만 바꾸거나 읽는 도구."""
    return ToolPolicy(
        None, USER, DataClass.USER_PRIVATE, DB_TIMEOUT, NO_RETRY, "tool_call_id 기록 재생", STEPS, display
    )


def public_lookup(service: str, display: str) -> ToolPolicy:
    return ToolPolicy(service, PUBLIC_ONLY, DataClass.PUBLIC, HTTP_TIMEOUT, READ_RETRY, "조회라 불필요", STEPS, display)


CALENDAR_WRITE = ToolPolicy(
    "Google Calendar",
    OWN_ACCOUNT,
    DataClass.USER_PRIVATE,
    HTTP_TIMEOUT,
    "쓰기는 재시도 없음, 결과 불명이면 operation_id로 재조회",
    "실행 계획·operation_id·고정 event id·If-Match",
    "steps.output + execution_receipts",
    "승인 카드(대상·계정·변경 전후) → 영수증 있을 때만 완료",
)
MAIL_READ = ToolPolicy(
    "Gmail",
    USER,
    DataClass.MAIL_PRIVATE,
    HTTP_TIMEOUT,
    READ_RETRY,
    "조회라 불필요",
    "steps.output에는 통 수만(results.output_for_storage), 모델 기록은 앞 500자 암호문(history.seal_mail_returns)",
    "외부 콘텐츠로 감싸 출처 표시",
)

TOOL_POLICIES: dict[str, ToolPolicy] = {
    "list_todos": internal("할 일 목록"),
    "list_notes": internal("메모 목록"),
    "list_memories": internal("기억 목록"),
    "list_schedules": internal("예약 목록"),
    "list_watches": internal("감시 목록"),
    "save_note": internal("저장한 메모"),
    "add_todo": internal("추가한 할 일"),
    "complete_todo": internal("완료한 할 일"),
    "remember": internal("저장한 기억(원문 출처 확인)"),
    "forget": internal("지운 기억"),
    "create_schedule": internal("만든 예약(주기·만료)"),
    "delete_schedule": internal("지운 예약"),
    "create_watch": internal("만든 감시(조건·만료)"),
    "delete_watch": internal("지운 감시"),
    "draft_message": internal("초안 카드(보내지 않음, 복사 버튼)"),
    "get_weather": public_lookup("Open-Meteo·카카오 좌표", "날씨와 출처"),
    "search_places": public_lookup("카카오 로컬", "장소 목록과 출처(결과에 없는 영업시간·가격은 말하지 않음)"),
    "web_search": public_lookup("네이버 검색", "검색 결과와 출처 링크"),
    "list_events": ToolPolicy(
        "Google Calendar", USER, DataClass.USER_PRIVATE, HTTP_TIMEOUT, READ_RETRY, "조회라 불필요", STEPS, "일정 목록"
    ),
    "create_event": CALENDAR_WRITE,
    "update_event": CALENDAR_WRITE,
    "delete_event": CALENDAR_WRITE,
    "list_mail": MAIL_READ,
    "read_mail": MAIL_READ,
}

MAIL_TOOLS = ("list_mail", "read_mail")
EVENT_TOOLS = ("list_events",)
MIN_TITLE_LENGTH = 2
MAIL_REFUSAL = (
    "이 작업에서 메일을 읽었기 때문에 외부 검색에 내용을 보내지 않았습니다. "
    "검색이 필요하면 공개해도 되는 검색어를 직접 입력해 주세요."
)
PRIVATE_REFUSAL = (
    "개인 일정 내용이 검색어에 들어 있어 외부 검색에 보내지 않았습니다. "
    "검색이 필요하면 공개해도 되는 검색어를 직접 입력해 주세요."
)
# 7단계 보강(사용자 결정 a): 메일을 읽은 문맥에서는 자동화를 만드는 도구를 주지 않는다
# (메일 속 지시로 예약·감시 생성 금지).
# 조회(list_*)와 지우기(delete_*)는 새 자동 실행을 만들지 않아 그대로 둔다(docs/DECISIONS.md 7단계 보강).
AUTOMATION_CREATE_TOOLS = ("create_schedule", "create_watch")
AUTOMATION_REFUSAL = (
    "이 대화에서 메일을 읽었기 때문에 예약·감시를 만들지 않았습니다. 필요하면 새 대화에서 직접 요청해 주세요."
)


def needs_check(tool_name: str) -> bool:
    policy = TOOL_POLICIES.get(tool_name)
    return policy is not None and policy.service is not None and policy.accepts == PUBLIC_ONLY


def event_titles(output: Any) -> list[str]:
    """list_events 결과(외부 콘텐츠로 감싼 모양)에서 일정 제목들."""
    content = ((output or {}).get("data") or {}).get("content") if isinstance(output, dict) else None
    events = content.get("events") if isinstance(content, dict) else None
    titles = [e.get("title") for e in events or [] if isinstance(e, dict)]
    return [t for t in titles if isinstance(t, str) and len(t.strip()) >= MIN_TITLE_LENGTH]


def mentions(args: dict[str, Any], titles: list[str]) -> bool:
    text = " ".join(str(v) for v in args.values() if isinstance(v, str))
    return any(title.strip() in text for title in titles)


# ---- 7단계 보강: 모델에게 보일 도구(run_toolsets의 filtered) ----


def returned_mail(content: Any) -> bool:
    """메일 도구 결과가 실제로 메일 내용을 돌려줬는가(성공). 거부·실패 결과에는 메일 내용이 없다."""
    if isinstance(content, ToolResult):
        return content.ok
    return isinstance(content, dict) and content.get("status") == "succeeded"


def mail_read_in(messages: Sequence[ModelMessage]) -> bool:
    """문맥(이전 대화 기록 + 이번 run)에 읽어 온 메일 결과가 있는가."""
    return any(
        isinstance(part, ToolReturnPart) and part.tool_name in MAIL_TOOLS and returned_mail(part.content)
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
    )


def hidden_after_mail(tool_name: str) -> bool:
    """메일을 읽은 문맥이면 주지 않는 도구: 공개 검색·장소·날씨와 자동화 생성(예약·감시)."""
    return needs_check(tool_name) or tool_name in AUTOMATION_CREATE_TOOLS


def visible_tool(ctx: RunContext[AgentDeps], tool_def: ToolDefinition) -> bool:
    """모델 요청마다 도구 목록을 거른다(BUILD_SPEC 2.2, 13절 7단계 "메일 작업의 도구 범위 제한").

    - 메일 도구: 메일 기능 플래그(PAYLOAD_ENCRYPTION_KEY)가 꺼져 있으면 보이지 않는다.
    - 공개 검색·장소·날씨(public만 받는 외부 도구)와 자동화 생성(create_schedule·create_watch): 문맥에 읽어 온
      메일이 있으면(이번 작업이든 같은 대화의 이전 작업이든) 보이지 않는다. 메일 처리 작업에는 공개 검색 도구를
      함께 주지 않고, 메일 속 지시로 예약·감시가 생기지 않게 한다. 실행 직전 검사(outbound_refusal,
      automation_refusal)는 두 번째 방어선으로 남긴다.
    """
    if tool_def.name in MAIL_TOOLS:
        return mail_enabled(ctx.deps.settings)
    if hidden_after_mail(tool_def.name):
        return not mail_read_in(ctx.messages)
    return True


async def mail_read_in_task(deps: AgentDeps) -> bool:
    """이번 task에서 끝난(성공한) 메일 단계가 있는가. 한 응답에서 메일 읽기와 함께 부른 호출은 아직 문맥에 결과가
    없으므로 steps로 본다."""
    async with deps.db() as session:
        found = await session.scalar(
            select(Step.id)
            .where(Step.task_id == deps.task_id, Step.status == StepStatus.DONE, Step.tool_name.in_(MAIL_TOOLS))
            .limit(1)
        )
    return found is not None


async def automation_refusal(deps: AgentDeps, messages: Sequence[ModelMessage], tool_name: str) -> ToolResult | None:
    """자동화 생성 도구의 실행 직전 검사(두 번째 방어선). 메일을 읽은 문맥이면 실패 결과, 아니면 None.

    판정은 visible_tool과 같다(문맥의 성공한 메일 결과). 여기에 이번 task의 끝난 메일 단계를 더한다.
    """
    if tool_name not in AUTOMATION_CREATE_TOOLS:
        return None
    if mail_read_in(messages) or await mail_read_in_task(deps):
        return ToolResult.failure(AUTOMATION_REFUSAL, error_code="data_policy")
    return None


async def refusal_before_run(ctx: RunContext[AgentDeps], tool_name: str, args: dict[str, Any]) -> ToolResult | None:
    """StepTracking이 도구 실행 직전에 부르는 데이터 정책 검사. 막아야 하면 실패 결과, 아니면 None."""
    refusal = await automation_refusal(ctx.deps, ctx.messages, tool_name)
    if refusal is not None:
        return refusal
    return await outbound_refusal(ctx.deps, tool_name, args)


async def outbound_refusal(deps: AgentDeps, tool_name: str, args: dict[str, Any]) -> ToolResult | None:
    """public만 받는 외부 도구에 개인 데이터가 실려 나가는지 실행 직전에 본다. 막아야 하면 결과, 아니면 None."""
    if not needs_check(tool_name):
        return None
    async with deps.db() as session:
        rows = await session.execute(
            select(Step.tool_name, Step.output).where(
                Step.task_id == deps.task_id,
                Step.status == StepStatus.DONE,
                Step.tool_name.in_((*MAIL_TOOLS, *EVENT_TOOLS)),
            )
        )
        steps = list(rows)
    if any(name in MAIL_TOOLS for name, _ in steps):
        return ToolResult.failure(MAIL_REFUSAL, error_code="data_policy")
    titles = [title for name, output in steps if name in EVENT_TOOLS for title in event_titles(output)]
    if mentions(args, titles):
        return ToolResult.failure(PRIVATE_REFUSAL, error_code="data_policy")
    return None
