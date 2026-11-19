"""자동화의 서버 제한 값과 시각 계산. DB를 쓰지 않는다.

명세 [예약 작업과 조건 감시]의 제한:
- 예약 작업: 사용자당 활성 10개, 하루 1회보다 잦을 수 없다(시각 하나 + 요일 목록이라 구조상 하루 1회 이하).
- 감시: READ 도구만, 메일 도구 불가, 간격 30분 이상, 사용자당 활성 10개, 기본 만료 30일.
명세에 없어 작업자가 정한 값(docs/DECISIONS.md 6단계):
- 감시 간격 최대 1,440분(하루). 충족 뒤 repeat=true면 12시간 뒤 다시(명세).
- 놓친 예약은 예정 시각에서 2시간 안이면 한 번만 따라잡고, 더 지났으면 건너뛰고 다음 시각으로 넘긴다.

진입점: store.py(서버 제한 강제), check.py(다음 시각·따라잡기·다시 알림), app/tools/automation.py(Interval의 ge/le),
routers/automations.py(max_active), schemas/conversations.py('예약 작업' 제목 금지). 근거는 BUILD_SPEC 8절,
DECISIONS 2026-11-17 "6단계 > 서버 제한"·"6단계 보강".

고칠 때 주의(값은 여기가 정본이지만 글자로 적힌 곳이 따로 있다):
- 제한 값을 바꾸면 도구 docstring·Field 설명(app/tools/automation.py의 "30 이상 1440 이하", "10개까지", "30일"),
  화면 안내(apps/web/components/AutomationList.tsx "30분…30일", AutomationItems.tsx "12시간"), 테스트를 같이 고친다.
  시스템 프롬프트(app/agent/prompts.py)에는 지금 이 숫자들이 없다.
- 감시 간격·만료·따라잡기 값을 바꿔도 이미 저장된 행의 next_run_at·expires_at은 그대로다.
"""

import json
import re
from collections.abc import Sequence
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.agent.results import MAIL_TOOLS as MAIL_TOOLS

# 예약 시각·요일의 기본 시간대. scheduled_jobs.timezone 기본값과 1분 검사 cron 시간대(workflows.py)와 같다.
DEFAULT_TIMEZONE = "Asia/Seoul"
SEOUL = ZoneInfo(DEFAULT_TIMEZONE)

# 명세 값: 사용자당 활성 예약 10개, 활성 감시 10개, 감시 간격 30분 이상, 감시 만료 30일.
# 켜진 것만 센다(꺼진 것은 자리를 차지하지 않는다). 검사는 store.py가 사용자 행을 잠근 뒤 한다.
MAX_ACTIVE_SCHEDULES = 10
MAX_ACTIVE_WATCHES = 10
MIN_WATCH_INTERVAL_MINUTES = 30
# 명세에 없어 정한 값(하루). DECISIONS 2026-11-17 6단계 "서버 제한".
MAX_WATCH_INTERVAL_MINUTES = 1440
WATCH_LIFETIME = timedelta(days=30)
# 다시 알림 최소 간격(명세의 12시간). 6단계 보강부터 "알린 뒤 12시간 쉬기"가 아니라 거짓→참 전환을 알릴 때
# 마지막 알림에서 이만큼 지났는지만 본다(check.should_notify).
WATCH_REPEAT_DELAY = timedelta(hours=12)
# 밀린 예약 유예 시간(작업자 결정). 07:30 브리핑을 오후에 받는 일은 막고, PC가 잠깐 잠든 정도는 받게 한 값이다
# (DECISIONS 2026-11-17 "밀린 예약"). 넘기면 skipped(missed)로 남기고 다음 시각으로 넘긴다.
SCHEDULE_CATCH_UP = timedelta(hours=2)
# 감시 도구 입력 JSON 최대 글자 수(명세에 없어 정한 값, DECISIONS 2026-11-17 6단계 "서버 제한").
MAX_TOOL_INPUT_CHARS = 1000

# 예약 작업 결과와 감시 충족 알림을 남기는 대화방(명세: '예약 작업' 대화방). 사용자마다 하나.
AUTOMATION_CONVERSATION_TITLE = "예약 작업"

# 아침 브리핑 기본 예약(명세). 프롬프트는 명세 문장 그대로다.
# BRIEFING_KIND는 scheduled_jobs.kind 값이라 바꾸면 기존 행과 맞지 않는다(완료 푸시 종류·화면 표시가 이 값을 본다).
# BRIEFING_PROMPT를 바꿔도 이미 만든 브리핑의 prompt_text는 그대로다(바꾸려면 마이그레이션, 예: 9d3727e1c857).
BRIEFING_TITLE = "아침 브리핑"
BRIEFING_KIND = "briefing"
# 6단계 보강: 화면이 처음 제안하는 시각(BUILD_SPEC 8.1). 사용자가 켜기 전에는 예약이 없다.
BRIEFING_TIME = time(7, 30)
# 6단계 보강: [제안] 줄 대신 구조화된 제안(suggestions)으로 남긴다(app/agent/replies.py).
BRIEFING_PROMPT = "오늘 일정, 날씨, 미완료 할 일을 요약해라. 행동이 필요한 항목은 제안(suggestions)으로 남겨라."
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


def seoul_day_text(moment: datetime) -> str:
    """'2026-11-18(수) 07:30'(서울). 다음 실행·확인처럼 요일이 중요한 값(2026-11-19, 분석 46번).

    요일을 붙여 모델이 날짜에서 요일을 직접 계산하다 틀리지 않게 한다. seoul_text는 [:10]으로 날짜만 자르는 곳이 있어
    모양을 바꾸지 않고 따로 둔다.
    """
    local = moment.astimezone(SEOUL)
    return f"{local:%Y-%m-%d}({'월화수목금토일'[local.weekday()]}) {local:%H:%M}"
