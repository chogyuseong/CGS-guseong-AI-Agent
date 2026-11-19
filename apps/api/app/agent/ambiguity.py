"""일정 수정·삭제 대상이 모호하면 승인 카드를 만들지 않고 되묻는다(2026-11-19, eval 07 회귀 대응).

진입점: app/agent/runner.py finish(승인 요청을 저장하기 직전). 테스트: tests/test_ambiguous_target.py.
왜: "치과 일정 5시로 옮겨줘"에 '치과'(11/19)와 '치과 정기검진'(11/26)이 있으면 모델이 하나를 골라 수정 승인을 요청하는
일이 4~5회 중 2회 있었다(프롬프트 보강으로도 그대로, docs/EVALS.md).
승인 카드가 마지막 방어선이지만 잘못된 대상의 카드를 띄우는 것 자체가 승인 범위 위반이라, 서버가 확정적으로 막는다.

규칙:
- 후보: 이번 작업의 마지막 일정 조회(list_events) 결과 중, 대상 일정과 제목 낱말(2글자 이상)이 하나라도 겹치는 일정.
- 후보가 둘 이상인데 사용자 메시지에 대상을 집어낼 단서가 없으면 모호하다. 단서: 그 일정의 날짜("19일", "11월 19일",
  "11/19"), 오늘·내일·모레(오늘 기준), 요일(다른 후보와 요일이 다를 때만), 시작 시각("3시", "15시", "15:00"),
  그 일정에만 있는 제목 낱말. 다른 후보에만 있는 제목 낱말을 말했으면 이 일정이 아니라고 본다.
- 조회 결과가 없거나 대상이 결과에 없으면 판단하지 않는다(막지 않는다 — 단서가 없는 판단으로 정상 요청을 막지 않게).

고칠 때 주의: 되묻기 문구의 "어느 일정"과 후보의 "M월 D일(요일) HH:MM" 모양은 tests/test_ambiguous_target.py가 찾는다.
막는 도구 목록(TARGET_TOOLS)을 바꾸면 docs/SAFETY.md도 고친다. DB를 읽기만 한다.

변경 이력:
- 2026-11-19 처음 작성.
"""

import re
from datetime import date, datetime, timedelta
from typing import Any

from pydantic_ai.tools import DeferredToolRequests
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import clock
from app.models import Message, Step, Task
from app.models.enums import StepStatus

TARGET_TOOLS = ("update_event", "delete_event")
WORD = re.compile(r"[0-9A-Za-z가-힣]{2,}")
WEEKDAYS = "월화수목금토일"
RELATIVE_DAYS = {"오늘": 0, "내일": 1, "모레": 2}


def today_seoul() -> date:
    return clock.now_utc().astimezone(clock.SEOUL).date()


def words(title: str) -> set[str]:
    return set(WORD.findall(title))


def start_of(event: dict[str, Any]) -> datetime | None:
    try:
        return datetime.fromisoformat(str(event.get("start")))
    except ValueError:
        return None


def candidates(events: list[dict[str, Any]], target: dict[str, Any]) -> list[dict[str, Any]]:
    """대상과 제목 낱말이 겹치는 일정들(대상 포함, 조회 순서)."""
    mine = words(str(target.get("title", "")))
    return [e for e in events if e is target or mine & words(str(e.get("title", "")))]


def date_clue(message: str, moment: datetime, today: date) -> bool:
    """메시지에 그 일정의 날짜가 있는가(일, 월·일, M/D, 오늘·내일·모레)."""
    day, month = moment.day, moment.month
    patterns = [rf"(?<!\d){month}\s*월\s*{day}\s*일", rf"(?<!\d){day}\s*일", rf"(?<!\d){month}/{day}(?!\d)"]
    if any(re.search(p, message) for p in patterns):
        return True
    return any(word in message and moment.date() == today + timedelta(days=k) for word, k in RELATIVE_DAYS.items())


def time_clue(message: str, moment: datetime) -> bool:
    """메시지에 그 일정의 시작 시각이 있는가("3시", "15시", "15:00"). 24시간·12시간 표기를 모두 본다."""
    hours = {moment.hour, moment.hour % 12 or 12}
    if any(re.search(rf"(?<!\d){h}\s*시", message) for h in hours):
        return True
    return f"{moment:%H:%M}" in message


def pinned(message: str, target: dict[str, Any], others: list[dict[str, Any]], today: date) -> bool:
    """사용자 메시지가 다른 후보가 아니라 이 일정을 집어 말했는가."""
    mine, theirs = words(str(target.get("title", ""))), set().union(*(words(str(o.get("title", ""))) for o in others))
    if any(w in message for w in theirs - mine):
        return False  # 다른 후보에만 있는 낱말(예: '정기검진')을 말했는데 이 일정을 골랐다
    if any(w in message for w in mine - theirs):
        return True
    moment = start_of(target)
    if moment is None:
        return False
    other_days = {m.weekday() for o in others if (m := start_of(o))}
    weekday = f"{WEEKDAYS[moment.weekday()]}요일"
    weekday_clue = weekday in message and moment.weekday() not in other_days
    return date_clue(message, moment, today) or time_clue(message, moment) or weekday_clue


def describe(event: dict[str, Any]) -> str:
    moment = start_of(event)
    when = f"{moment.month}월 {moment.day}일({WEEKDAYS[moment.weekday()]}) {moment:%H:%M}" if moment else "날짜 미상"
    return f"{event.get('title', '')}({when})"


async def last_events(session: AsyncSession, task_id: int) -> list[dict[str, Any]]:
    """이번 작업에서 마지막으로 성공한 일정 조회의 일정 목록(id·title·start). 없으면 빈 목록."""
    output = await session.scalar(
        select(Step.output)
        .where(Step.task_id == task_id, Step.tool_name == "list_events", Step.status == StepStatus.DONE)
        .order_by(Step.order.desc())
        .limit(1)
    )
    content = ((output or {}).get("data") or {}).get("content") if isinstance(output, dict) else None
    events = content.get("events") if isinstance(content, dict) else None
    return [e for e in events or [] if isinstance(e, dict)]


async def trigger_text(session: AsyncSession, task_id: int, user_id: int) -> str:
    """작업을 시작한 사용자 메시지(user_id 조건). 없으면 빈 글자."""
    task = await session.scalar(select(Task).where(Task.id == task_id, Task.user_id == user_id))
    message = await session.get(Message, task.trigger_message_id) if task and task.trigger_message_id else None
    return message.content if message else ""


async def ambiguity_question(
    session: AsyncSession, task_id: int, user_id: int, deferred: DeferredToolRequests
) -> str | None:
    """모호한 수정·삭제 대상이 있으면 되묻는 문장, 아니면 None. DB를 읽기만 한다."""
    calls = [c for c in deferred.approvals if c.tool_name in TARGET_TOOLS]
    if not calls:
        return None
    events = await last_events(session, task_id)
    message = await trigger_text(session, task_id, user_id)
    for call in calls:
        target = next((e for e in events if e.get("id") == call.args_as_dict().get("event_id")), None)
        if target is None:
            continue
        group = candidates(events, target)
        others = [e for e in group if e is not target]
        if others and not pinned(message, target, others, today_seoul()):
            listing = ", ".join(describe(e) for e in group)
            return (
                f"어느 일정인지 확인이 필요해 승인 카드를 만들지 않았습니다: {listing}. "
                "바꿀 일정을 날짜나 제목으로 말씀해 주세요."
            )
    return None
