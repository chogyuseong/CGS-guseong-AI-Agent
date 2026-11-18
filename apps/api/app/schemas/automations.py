"""자동화 화면 API 응답(6단계). 웹은 이 모양을 OpenAPI에서 타입으로 생성한다.

라우터는 app/routers/automations.py, 화면 사용처는 apps/web/lib/automations.ts.

고칠 때 주의:
- WatchStatus·WatchUnknownReason은 감시 실행 쪽(app/automation/check.py·watch.py·conditions.py)이
  watches.last_result에 쓰는 값과 같아야 한다.
  모르는 값이 기록되면 라우터의 result_out이 그 결과를 None으로 버린다(경고 로그만).
- AutomationErrorCode는 app/automation/store.py의 AutomationError code와 같아야 한다.
- BriefingUpdate의 시각 패턴(24시간 HH:MM)을 바꾸면 store.enable_briefing의 해석도 확인한다.
- 관련 테스트: tests/test_automation_api.py, test_briefing_consent.py, test_budget_pause.py, test_watch_conditions.py.
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.models.enums import TaskStatus


class LastTaskOut(BaseModel):
    """예약 작업의 마지막 실행이 만든 task.

    결과 글은 '예약 작업' 대화방(conversation_id)에도 assistant 메시지로 있다.
    """

    id: int
    conversation_id: int
    status: TaskStatus
    final_answer: str | None
    error: str | None
    updated_at: datetime


class ScheduleOut(BaseModel):
    id: int
    title: str
    prompt_text: str
    # 서울 기준 'HH:MM'
    time_of_day: str
    # 0=월 ... 6=일
    weekdays: list[int]
    # 사람이 읽는 반복 주기. 예: '매일 07:30', '매주 일요일 21:00'
    repeat_text: str
    is_active: bool
    # 꺼져 있어도 값은 있다(켤 때 다시 계산한다). 화면은 is_active일 때만 '다음 실행'으로 보여 준다.
    next_run_at: datetime
    last_run_at: datetime | None
    # 아침 브리핑 기본 예약인가(완료 푸시가 '브리핑이 준비되었습니다')
    is_briefing: bool
    last_task: LastTaskOut | None


class ScheduleListOut(BaseModel):
    """GET /api/schedules. schedules는 시각 순. max_active는 켜 둘 수 있는 최대 개수(10)."""

    schedules: list[ScheduleOut]
    max_active: int
    # 6단계 보강: 오늘 하루 비용 한도에 닿았다(자동 작업은 내일 서울 자정이 지나면 다시 돈다)
    budget_paused: bool = False


# unknown(6단계 보강): 조회·판정 실패, 값 없음(reason). tool_failed·judge_failed는 보강 전 기록에만 있다.
type WatchStatus = Literal[
    "met", "not_met", "unknown", "tool_failed", "judge_failed", "skipped_budget", "paused_budget", "expired"
]
type WatchUnknownReason = Literal["tool_failed", "judge_failed", "value_missing"]


class WatchResultOut(BaseModel):
    """감시의 마지막 확인 결과(watches.last_result).

    status: met(조건 충족) | not_met | unknown(조회·판정 실패, 값 없음: reason)
    | paused_budget(하루 비용 상한으로 건너뜀)
    | expired(만료로 꺼짐). tool_failed·judge_failed·skipped_budget은 6단계 보강 전 기록.
    """

    status: WatchStatus
    reason: WatchUnknownReason | None = None
    checked_at: datetime
    summary: str = ""
    error: str | None = None
    sources: list[str] = Field(default_factory=list)


class WatchOut(BaseModel):
    id: int
    description: str
    tool_name: str
    tool_input: dict[str, Any]
    condition_text: str
    interval_minutes: int
    repeat: bool
    is_active: bool
    next_run_at: datetime
    expires_at: datetime
    last_notified_at: datetime | None
    last_result: WatchResultOut | None


class WatchListOut(BaseModel):
    """GET /api/watches. watches는 만든 순. max_active는 켜 둘 수 있는 최대 개수(10)."""

    watches: list[WatchOut]
    max_active: int
    # 6단계 보강: 오늘 하루 비용 한도에 닿았다(감시 확인은 paused_budget으로 건너뛴다)
    budget_paused: bool = False


class BriefingUpdate(BaseModel):
    """PUT /api/schedules/briefing 본문: 매일 실행할 시각(서울, 24시간 'HH:MM')."""

    time_of_day: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$", examples=["07:30"])


class ActiveUpdate(BaseModel):
    """PATCH 본문: 켜기(true)·끄기(false)."""

    is_active: bool


# 자동화 API 오류 code. not_found만 404, 나머지는 409(라우터 http_error).
type AutomationErrorCode = Literal["not_found", "limit_reached", "duplicate", "expired", "invalid"]


class AutomationErrorDetail(BaseModel):
    # 화면은 문구가 아니라 code로 분기한다.
    code: AutomationErrorCode
    # 사용자에게 보여 줄 한국어 문장
    message: str


class AutomationErrorOut(BaseModel):
    """자동화 API의 404·409 응답 본문."""

    detail: AutomationErrorDetail
