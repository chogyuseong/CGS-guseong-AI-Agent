"""자동화 화면 API 응답(6단계). 웹은 이 모양을 OpenAPI에서 타입으로 생성한다."""

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


type WatchStatus = Literal["met", "not_met", "tool_failed", "judge_failed", "skipped_budget", "expired"]


class WatchResultOut(BaseModel):
    """감시의 마지막 확인 결과(watches.last_result).

    status: met(조건 충족, 대화방에 알림) | not_met | tool_failed(조회 실패) | judge_failed(판정 실패, met=false로 봄)
    | skipped_budget(하루 비용 상한으로 건너뜀) | expired(만료로 꺼짐)
    """

    status: WatchStatus
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


class ActiveUpdate(BaseModel):
    """PATCH 본문: 켜기(true)·끄기(false)."""

    is_active: bool


type AutomationErrorCode = Literal["not_found", "limit_reached", "duplicate", "expired", "invalid"]


class AutomationErrorDetail(BaseModel):
    # 화면은 문구가 아니라 code로 분기한다.
    code: AutomationErrorCode
    # 사용자에게 보여 줄 한국어 문장
    message: str


class AutomationErrorOut(BaseModel):
    """자동화 API의 404·409 응답 본문."""

    detail: AutomationErrorDetail
