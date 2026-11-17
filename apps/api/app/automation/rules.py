"""자동화의 서버 제한 값과 시각 계산. DB를 쓰지 않는다.

명세 [예약 작업과 조건 감시]의 제한:
- 예약 작업: 사용자당 활성 10개, 하루 1회보다 잦을 수 없다(시각 하나 + 요일 목록이라 구조상 하루 1회 이하).
- 감시: READ 도구만, 메일 도구 불가, 간격 30분 이상, 사용자당 활성 10개, 기본 만료 30일.
명세에 없어 작업자가 정한 값(docs/DECISIONS.md 6단계):
- 감시 간격 최대 1,440분(하루). 충족 뒤 repeat=true면 12시간 뒤 다시(명세).
- 놓친 예약은 예정 시각에서 2시간 안이면 한 번만 따라잡고, 더 지났으면 건너뛰고 다음 시각으로 넘긴다.
"""

import json
import re
from collections.abc import Sequence
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.agent.results import MAIL_TOOLS as MAIL_TOOLS

DEFAULT_TIMEZONE = "Asia/Seoul"
SEOUL = ZoneInfo(DEFAULT_TIMEZONE)

MAX_ACTIVE_SCHEDULES = 10
MAX_ACTIVE_WATCHES = 10
MIN_WATCH_INTERVAL_MINUTES = 30
MAX_WATCH_INTERVAL_MINUTES = 1440
WATCH_LIFETIME = timedelta(days=30)
WATCH_REPEAT_DELAY = timedelta(hours=12)
SCHEDULE_CATCH_UP = timedelta(hours=2)
MAX_TOOL_INPUT_CHARS = 1000

# 예약 작업 결과와 감시 충족 알림을 남기는 대화방(명세: '예약 작업' 대화방). 사용자마다 하나.
AUTOMATION_CONVERSATION_TITLE = "예약 작업"

# 아침 브리핑 기본 예약(명세). 프롬프트는 명세 문장 그대로다.
BRIEFING_TITLE = "아침 브리핑"
BRIEFING_KIND = "briefing"
# 6단계 보강: 화면이 처음 제안하는 시각(BUILD_SPEC 8.1). 사용자가 켜기 전에는 예약이 없다.
BRIEFING_TIME = time(7, 30)
BRIEFING_PROMPT = "오늘 일정, 날씨, 미완료 할 일을 요약해라. 행동이 필요한 항목은 [제안] 줄로만 적어라."
EVERY_DAY = (0, 1, 2, 3, 4, 5, 6)

# 감시에 쓸 수 없는 READ 도구: 메일(명세), 자동화 목록(감시가 자기 설정을 보는 것은 뜻이 없다).
# MAIL_TOOLS는 app/agent/results.py에 있다(steps 저장 500자 규칙도 같은 목록을 쓴다). 7단계부터 메일 도구는
# read_tools가 아닌 mail_tools 묶음(chat 전용)이라 감시 도구 목록에 애초에 없지만, 이름으로 한 번 더 막는다.
NOT_WATCHABLE_TOOLS = MAIL_TOOLS | {"list_schedules", "list_watches"}

WEEKDAY_NAMES = ["월", "화", "수", "목", "금", "토", "일"]
TIME_PATTERN = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def parse_time_of_day(value: str) -> time | None:
    """'HH:MM'(24시간)만 받는다. 아니면 None."""
    match = TIME_PATTERN.match(value.strip())
    return time(int(match.group(1)), int(match.group(2))) if match else None


DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def parse_date(value: object) -> date | None:
    """'YYYY-MM-DD' 실제 날짜만. '내일' 같은 말이나 없는 날짜는 None."""
    if not isinstance(value, str) or not DATE_PATTERN.match(value):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def clean_weekdays(weekdays: list[int]) -> list[int] | None:
    """0(월)~6(일)만, 중복을 빼고 정렬한다. 비었거나 범위 밖이면 None."""
    if not weekdays or any(day not in range(7) for day in weekdays):
        return None
    return sorted(set(weekdays))


def same_text_key(value: str) -> str:
    """중복 비교용: 공백을 하나로, 대소문자 무시."""
    return " ".join(value.split()).casefold()


def input_key(tool_input: dict[str, Any]) -> str:
    """중복 비교용: 키 순서와 상관없는 JSON."""
    return json.dumps(tool_input, ensure_ascii=False, sort_keys=True)


def next_schedule_run(
    time_of_day: time, weekdays: Sequence[int], after: datetime, timezone: str = DEFAULT_TIMEZONE
) -> datetime:
    """after보다 뒤인(같은 시각은 제외) 가장 이른 실행 시각. 시각·요일은 timezone(기본 서울) 기준이다."""
    zone = ZoneInfo(timezone)
    local = after.astimezone(zone)
    for offset in range(8):
        day = local.date() + timedelta(days=offset)
        candidate = datetime.combine(day, time_of_day, tzinfo=zone)
        if day.weekday() in weekdays and candidate > local:
            return candidate
    raise ValueError("요일 목록이 비어 있다")


def repeat_text(time_of_day: time, weekdays: Sequence[int]) -> str:
    """'매일 07:30', '평일 09:00', '매주 일요일 21:00', '매주 월·금요일 09:00'."""
    days = sorted(set(weekdays))
    clock = time_of_day.strftime("%H:%M")
    if days == list(EVERY_DAY):
        return f"매일 {clock}"
    if days == [0, 1, 2, 3, 4]:
        return f"평일 {clock}"
    if days == [5, 6]:
        return f"주말 {clock}"
    return f"매주 {'·'.join(WEEKDAY_NAMES[day] for day in days)}요일 {clock}"


def seoul_text(moment: datetime) -> str:
    """'2026-11-18 07:30'(서울). 도구 결과에서 모델이 그대로 말할 수 있게."""
    return moment.astimezone(SEOUL).strftime("%Y-%m-%d %H:%M")
