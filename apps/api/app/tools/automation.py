"""자동화 도구 (6단계): create_schedule·delete_schedule·create_watch·delete_watch = INTERNAL,
list_schedules·list_watches = READ (toolsets.py에서 정한다. 명세 도구 목록).

- '매주/매일 ~해줘'는 create_schedule, '~하면 알려줘'는 create_watch(시스템 프롬프트). 등록 결과에 주기와 만료일을 넣어
  모델이 그대로 말할 수 있게 한다.
- 서버 제한(활성 10개, 감시 간격 30분 이상, READ 도구만, 메일 불가, 중복 거절)은 app/automation/store.py가 강제한다.
  인자 제약(Field)은 모델에게 보이는 안내이자 첫 번째 검사이고, 도구 함수를 직접 불러도 store가 다시 막는다.
- 만든 예약·감시는 자동 작업이라 READ 도구만으로 실행된다(절대 원칙 4). 자동 작업에는 이 생성 도구가 없으므로
  예약 작업이 스스로 예약·감시를 늘릴 수 없다.

진입점: 모델이 chat task에서 부른다(toolsets.py의 read_tools·internal_tools, TOOLSETS_BY_ORIGIN). 같은 일을 화면에서
하는 API는 app/routers/automations.py이고, 둘 다 app/automation/store.py를 거친다(제한 검사가 한 곳).
외부 API 호출 없음. 근거: BUILD_SPEC 8절(자동화), docs/DECISIONS.md "6단계"·"6단계 보강"(2026-11-17).

변경 이력:
- 2026-11-17 6단계: 도구 6개, 서버 제한은 store가 강제.
- 2026-11-17 6단계 보강: create_watch에 숫자 조건(number_field·number_op·number_value)을 더함
  (서버 비교, 모델 비용 없음).
- 7단계 보강·2026-11-18: 메일이나 웹 검색 결과를 읽은 문맥에서는 create_schedule·create_watch를 모델에게 보이지 않고
  실행 직전에도 막는다. 이 파일이 아니라 app/agent/data_policy.py(visible_tool, automation_refusal)가 한다.

고칠 때 주의:
- 제한 값(활성 10개, 간격 30~1440분, 만료 30일, 도구 입력 1,000자)은 app/automation/rules.py가 정본이다. Interval의
  ge/le는 rules를 읽지만 도구 docstring과 Field description 속 숫자("30 이상 1440 이하", "10개까지", "30일")는
  글자라서 rules를 바꾸면 같이 고친다.
- 도구 이름을 바꾸거나 늘리면: toolsets.py 등록 목록, 아래 LABELS, data_policy.TOOL_POLICIES(tests/test_data_policy.py가
  빠진 도구를 잡는다), data_policy.AUTOMATION_CREATE_TOOLS(생성 도구일 때), rules.NOT_WATCHABLE_TOOLS(조회 도구일 때).
- NumberField 설명의 예시 키(precipitation_probability_max, todos)는 weather.summarize·notes.list_todos 결과의 키다.
  그 키를 바꾸면 이미 저장된 감시의 condition_spec이 값을 못 읽어 unknown이 된다(app/automation/conditions.py).
- 모델에게 돌려주는 결과 모양(schedule_view·watch_view)을 바꾸면 eval(evals/)과
  tests/test_automation_tools.py를 확인한다.
- 테스트: tests/test_automation_tools.py, test_automation_api.py, test_automation_runs.py, test_mail_automation.py,
  test_search_automation.py.
"""

from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import Field
from pydantic_ai import RunContext

from app import clock
from app.agent.data_policy import outbound_refusal
from app.agent.deps import AgentDeps
from app.agent.results import ToolResult
from app.automation import rules, store
from app.automation.store import AutomationError
from app.automation.watchable import WatchToolError, validated_input, watchable_tool
from app.models import ScheduledJob, Watch
from app.tools import coupang
from app.tools.notes import short

ScheduleTitle = Annotated[str, Field(min_length=1, max_length=100, description="예약 이름. 예: 다음 주 일정 요약")]
SchedulePrompt = Annotated[
    str,
    Field(
        min_length=1, max_length=1000, description="정한 시각에 비서에게 시킬 요청 문장. 예: 다음 주 일정을 요약해줘"
    ),
]
TimeOfDay = Annotated[str, Field(pattern=r"^\d{2}:\d{2}$", description="서울 기준 24시간 'HH:MM'. 예: 21:00")]
Weekdays = Annotated[
    list[int],
    Field(
        min_length=1,
        max_length=7,
        description="실행 요일 번호 목록. 0=월 1=화 2=수 3=목 4=금 5=토 6=일. 매일이면 0~6 전부",
    ),
]
WatchDescription = Annotated[str, Field(min_length=1, max_length=200, description="감시 이름. 예: 내일 서울 비 소식")]
WatchToolName = Annotated[
    str, Field(min_length=1, max_length=100, description="주기적으로 실행할 조회 도구 이름. 예: get_weather")
]
WatchToolInput = Annotated[
    dict[str, Any], Field(description="그 도구에 넣을 인자. 예: {'place': '서울', 'date': '2026-11-18'}")
]
Condition = Annotated[
    str, Field(min_length=1, max_length=500, description="알려 줄 조건. 예: 강수 확률이 60%를 넘는다")
]
Interval = Annotated[
    int,
    Field(
        ge=rules.MIN_WATCH_INTERVAL_MINUTES,
        le=rules.MAX_WATCH_INTERVAL_MINUTES,
        description="확인 간격(분). 30 이상 1440 이하",
    ),
]
Repeat = Annotated[
    bool,
    Field(
        description="true면 알린 뒤에도 계속 감시하고 조건이 거짓→참으로 다시 바뀔 때 알린다(최소 12시간 간격). "
        "false면 한 번 알리고 끝"
    ),
]
NumberField = Annotated[
    str | None,
    Field(
        max_length=100,
        description="숫자 조건일 때: 조회 결과에서 비교할 값의 키(점으로 연결). 예: get_weather는 "
        "precipitation_probability_max, 할 일 개수는 list_todos의 todos",
    ),
]
NumberOp = Annotated[Literal[">", ">=", "<", "<=", "=="] | None, Field(description="숫자 조건의 비교")]
NumberValue = Annotated[float | None, Field(description="숫자 조건의 기준 값. 예: 60")]


def now_utc() -> datetime:
    """지금(app.clock). eval은 사례의 고정 시각을 쓴다.

    다음 실행 시각·감시 만료일 계산의 기준이다. datetime.now()를 직접 쓰면 eval의 고정 시각(evals/harness.py
    frozen_clock)이 먹지 않아 '내일' 감시의 만료일이 실행 날짜마다 달라진다.
    """
    return clock.now_utc()


def schedule_view(job: ScheduledJob) -> dict[str, Any]:
    """모델에게 돌려줄 예약 한 건. 주기·다음 실행을 서울 기준 문장으로 넣는다.

    모델이 시각을 직접 계산하지 않고 그대로 말하게 하려는 것이다.

    꺼진 예약은 next_run이 None이다(다음 실행이 없다고 말하게). DB를 읽거나 쓰지 않는다.
    """
    return {
        "id": job.id,
        "title": job.title,
        "prompt_text": job.prompt_text,
        "repeat_text": rules.repeat_text(job.time_of_day, job.weekdays),
        "is_active": job.is_active,
        "next_run": rules.seoul_text(job.next_run_at) if job.is_active else None,
    }


def watch_view(watch: Watch) -> dict[str, Any]:
    """모델에게 돌려줄 감시 한 건. 만료일은 서울 날짜(YYYY-MM-DD)만 준다(도구 설명이 "만료일을 말하라"고 한다).

    DB를 읽거나 쓰지 않는다.
    """
    return {
        "id": watch.id,
        "description": watch.description,
        "tool_name": watch.tool_name,
        "tool_input": watch.tool_input,
        "condition_text": watch.condition_text,
        "condition_type": watch.condition_type,
        "interval_minutes": watch.interval_minutes,
        "repeat": watch.repeat,
        "is_active": watch.is_active,
        "expires_on": rules.seoul_text(watch.expires_at)[:10],
    }


# 도구(INTERNAL). docstring은 모델에게 보이는 도구 설명이라 여기 주석으로 적는다.
# - store.add_schedule이 사용자 행을 FOR UPDATE로 잠근 뒤 중복·활성 10개를 검사한다. 같은 사용자의 두 요청이 동시에
#   와도 상한을 넘지 못하게 하려는 것이다(DECISIONS 6단계 "서버 제한").
# - 거절(AutomationError)이면 커밋하지 않고 세션을 닫아 아무것도 남지 않는다. 성공이면 한 트랜잭션으로 커밋한다.
# - 모델이 Field 제약을 지켜도, 도구를 직접 불러도 store가 같은 검사를 다시 한다(모델 지시만으로 제한을 넘지 못함).
async def create_schedule(
    ctx: RunContext[AgentDeps],
    title: ScheduleTitle,
    prompt_text: SchedulePrompt,
    time_of_day: TimeOfDay,
    weekdays: Weekdays,
) -> ToolResult:
    """정한 요일·시각(서울)마다 요청 문장을 자동으로 실행하는 예약 작업을 만든다.
    하루 한 번까지, 켜 둔 예약은 10개까지. 예약 실행은 조회만 하고 결과를 '예약 작업' 대화방에 남긴다.
    만든 뒤 반복 주기와 다음 실행 시각을 사용자에게 말한다."""
    async with ctx.deps.db() as session:
        try:
            job = await store.add_schedule(
                session,
                ctx.deps.user_id,
                title=title,
                prompt_text=prompt_text,
                time_text=time_of_day,
                weekdays=weekdays,
                now=now_utc(),
            )
        except AutomationError as error:
            return ToolResult.failure(error.message, error_code=error.code)
        await session.commit()
    return ToolResult.success({**schedule_view(job), "saved_to": "앱 예약 작업(자동화 화면)"})


# 도구(READ). 예약 작업 task(origin=schedule)도 read_tools로 받지만 감시에는 쓸 수 없다(rules.NOT_WATCHABLE_TOOLS).
async def list_schedules(ctx: RunContext[AgentDeps]) -> ToolResult:
    """내 예약 작업 목록(꺼진 것 포함)과 각 반복 주기·다음 실행 시각."""
    async with ctx.deps.db() as session:
        rows = await store.list_schedules(session, ctx.deps.user_id)
    return ToolResult.success({"schedules": [schedule_view(job) for job in rows]})


# 도구(INTERNAL). store.owned_schedule이 user_id 조건으로 찾으므로 남의 예약 id는 not_found로 끝난다.
# 이미 잡혀 실행 중인 예약은 끝까지 돈다(DECISIONS 6단계 보강 "예약 삭제·끄기와 실행 경합").
async def delete_schedule(ctx: RunContext[AgentDeps], schedule_id: int) -> ToolResult:
    """예약 작업 하나를 지운다. schedule_id는 list_schedules 결과의 id."""
    async with ctx.deps.db() as session:
        try:
            await store.delete_schedule(session, ctx.deps.user_id, schedule_id)
        except AutomationError as error:
            return ToolResult.failure(error.message, error_code=error.code)
        await session.commit()
    return ToolResult.success({"deleted_id": schedule_id})


# 도구(INTERNAL). 서버 검사는 store.add_watch 순서대로: 간격 → 숫자 조건 모양 → 만료일(date 인자면 그 날
# 서울 자정, 지난 날짜 거부) → 감시할 수 있는 도구·입력(automation/watchable.py, READ 도구만·메일 제외)
# → 사용자 잠금 → 중복·활성 10개.
# 메일 도구는 감시에 쓸 수 없다(명세 금지, toolsets.py mail_tools 주석·DECISIONS 7단계).
# READ만 허용하는 것은 절대 원칙 7.
# 아래 세 숫자 칸 검사는 "일부만 넣은 숫자 조건"이 문장 조건으로 조용히 바뀌어 모델 판정 비용이 드는 일을 막는다.
async def prepare_price_watch(deps: AgentDeps, fields: dict[str, Any]) -> ToolResult | None:
    """등록 시 공개한 상품명과 목표 금액을 고정한다. 실패하면 DB를 쓰거나 공급자를 호출하지 않는다.

    2026-11-18 목표가 감시. 검색 결과·메일을 본 대화에서의 등록 금지는 일반 create_watch 정책을 그대로 거친다.
    """
    try:
        args = validated_input(watchable_tool("coupang_price"), fields["tool_input"])
    except WatchToolError as error:
        return ToolResult.failure(error.message, error_code="invalid")
    fields["tool_input"] = args
    product = args.get("product")
    if not isinstance(product, str) or not await coupang.public_product(deps, product):
        return ToolResult.failure("현재 요청에 감시할 공개 상품명을 직접 적어 주세요.", error_code="data_policy")
    refusal = await outbound_refusal(deps, "compare_products", {"product": product})
    if refusal is not None:
        return refusal
    spec = fields.get("condition_spec") or {}
    target = target_price(spec)
    if target is None:
        return ToolResult.failure(
            "목표가는 원 단위 양수이며 price의 미만 또는 이하 조건이어야 합니다.", error_code="invalid"
        )
    if not (
        deps.settings.coupang_access_key.get_secret_value().strip()
        and deps.settings.coupang_secret_key.get_secret_value().strip()
    ):
        return ToolResult.failure("쿠팡 파트너스 키를 먼저 설정해 주세요.", error_code="not_configured")
    basis = "배송비 포함" if args.get("include_shipping") else "표시가"
    comparison = "미만" if spec["op"] == "<" else "이하"
    options = [product, *(args.get("required") or [])]
    if args.get("new_only"):
        options.append("신품")
    if excluded := args.get("excluded"):
        options.append("제외: " + ", ".join(excluded))
    fields["condition_text"] = " · ".join([*options, f"{basis} {target:,}원 {comparison}"])
    return None


def target_price(spec: dict[str, Any]) -> int | None:
    """목표가 조건(price < 또는 <= 원 단위 양의 정수)이면 그 금액, 아니면 None.

    숫자 감시의 기준은 float로 저장된다(conditions). 2**53 미만이어야 원 단위 정수가 정확히 남는다.
    bool은 int의 하위형이라 True가 1원으로 통과하지 않게 따로 막는다. 소수 금액(19900.5)도 거절한다.
    """
    value = spec.get("value")
    if spec.get("field") != "price" or spec.get("op") not in ("<", "<="):
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    if not (0 < value < 2**53) or int(value) != value:
        return None
    return int(value)


async def create_watch(
    ctx: RunContext[AgentDeps],
    description: WatchDescription,
    tool_name: WatchToolName,
    tool_input: WatchToolInput,
    condition_text: Condition,
    interval_minutes: Interval,
    repeat: Repeat = False,
    number_field: NumberField = None,
    number_op: NumberOp = None,
    number_value: NumberValue = None,
) -> ToolResult:
    """조회 도구를 정한 간격으로 실행해 조건이 맞으면 알려 주는 감시를 만든다. 조회(READ) 도구만 쓸 수 있고 메일 도구는
    안 된다. 간격 30분 이상, 켜 둔 감시 10개까지, 30일 뒤 만료.
    날짜는 실제 날짜(YYYY-MM-DD)로 넣고, 그 날이 지나면 끝난다.
    '강수 확률 60% 초과'처럼 숫자로 비교할 수 있는 조건은 number_field·number_op·number_value를 함께 넣는다
    (서버가 비교해 비용이 들지 않는다). 만든 뒤 확인 간격과 만료일을 사용자에게 말한다."""
    number = (number_field, number_op, number_value)
    if any(part is not None for part in number) and any(part is None for part in number):
        return ToolResult.failure(
            "숫자 조건은 number_field, number_op, number_value를 함께 넣어야 합니다.", error_code="invalid"
        )
    spec = None if number_field is None else {"field": number_field, "op": number_op, "value": number_value}
    fields = {
        "description": description,
        "tool_name": tool_name,
        "tool_input": tool_input,
        "condition_text": condition_text,
        "interval_minutes": interval_minutes,
        "repeat": repeat,
        "condition_spec": spec,
    }
    if tool_name == "coupang_price":
        refusal = await prepare_price_watch(ctx.deps, fields)
        if refusal is not None:
            return refusal
    return await save_watch(ctx.deps, fields)


async def save_watch(deps: AgentDeps, fields: dict[str, Any]) -> ToolResult:
    """검사를 통과한 감시를 자기 계정에 한 번 커밋한다. 기존 중복·만료·개수 제한은 store가 담당한다."""
    async with deps.db() as session:
        try:
            watch = await store.add_watch(session, deps.user_id, fields=fields, now=now_utc())
        except AutomationError as error:
            return ToolResult.failure(error.message, error_code=error.code)
        await session.commit()
    return ToolResult.success({**watch_view(watch), "saved_to": "앱 조건 감시(자동화 화면)"})


# 도구(READ). list_schedules와 같이 감시에는 쓸 수 없다(감시가 자기 설정을 보는 것은 뜻이 없다, DECISIONS 6단계).
async def list_watches(ctx: RunContext[AgentDeps]) -> ToolResult:
    """내 감시 목록(꺼진 것 포함)과 각 간격·만료일."""
    async with ctx.deps.db() as session:
        rows = await store.list_watches(session, ctx.deps.user_id)
    return ToolResult.success({"watches": [watch_view(watch) for watch in rows]})


# 도구(INTERNAL). user_id 조건으로 찾는다(남의 감시 id는 not_found).
async def delete_watch(ctx: RunContext[AgentDeps], watch_id: int) -> ToolResult:
    """감시 하나를 지운다. watch_id는 list_watches 결과의 id."""
    async with ctx.deps.db() as session:
        try:
            await store.delete_watch(session, ctx.deps.user_id, watch_id)
        except AutomationError as error:
            return ToolResult.failure(error.message, error_code=error.code)
        await session.commit()
    return ToolResult.success({"deleted_id": watch_id})


# ---- 타임라인 문구 (step.label) ----
# toolsets.TOOL_LABELS가 모은다. 도구 이름 키가 빠지면 "도구이름 실행하는 중"으로 보인다(toolsets.label_for).

LABELS: dict[str, Callable[[dict[str, Any]], str]] = {
    "create_schedule": lambda args: f"예약 작업 '{short(args.get('title', ''))}' 만드는 중",
    "list_schedules": lambda args: "예약 작업 목록 확인하는 중",
    "delete_schedule": lambda args: f"예약 작업 #{args.get('schedule_id', '?')} 지우는 중",
    "create_watch": lambda args: f"감시 '{short(args.get('description', ''))}' 만드는 중",
    "list_watches": lambda args: "감시 목록 확인하는 중",
    "delete_watch": lambda args: f"감시 #{args.get('watch_id', '?')} 지우는 중",
}
