"""6단계 자동화 도구 테스트: create·list·delete_schedule, create·list·delete_watch와 서버 제한.

테스트 10: READ가 아닌 도구나 메일 도구로 watch를 만들 수 없다. 간격 30분 미만도 거부.
도구 함수를 직접 부르는 테스트는 Pydantic AI의 인자 검증을 거치지 않으므로, 서버 코드의 검사만으로 막히는지 본다.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from pydantic_ai.messages import ToolCallPart
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.agent import MyAgent
from app.agent.deps import AgentDeps
from app.agent.results import ToolResult
from app.agent.toolsets import TOOLSETS_BY_ORIGIN
from app.automation import rules
from app.models import ScheduledJob, Watch
from app.models.enums import TaskOrigin
from app.tools import automation as tools
from tests.conftest import message_body, payload, tool_context
from tests.model_scripts import scripted, text

TOMORROW = (datetime.now(rules.SEOUL) + timedelta(days=1)).date().isoformat()


async def create_watch(deps: AgentDeps, **overrides: Any) -> ToolResult:
    args: dict[str, Any] = {
        "description": "내일 서울 비 소식",
        "tool_name": "get_weather",
        "tool_input": {"place": "서울", "date": TOMORROW},
        "condition_text": "강수 확률이 60%를 넘는다",
        "interval_minutes": 60,
        "repeat": False,
    }
    args.update(overrides)
    return await tools.create_watch(tool_context(deps, "create_watch"), **args)


async def create_schedule(deps: AgentDeps, **overrides: Any) -> ToolResult:
    args: dict[str, Any] = {
        "title": "다음 주 일정 요약",
        "prompt_text": "다음 주 일정을 요약해줘",
        "time_of_day": "21:00",
        "weekdays": [6],
    }
    args.update(overrides)
    return await tools.create_schedule(tool_context(deps, "create_schedule"), **args)


async def count(db_session: AsyncSession, model: type[ScheduledJob] | type[Watch], user_id: int) -> int:
    return await db_session.scalar(select(func.count()).select_from(model).where(model.user_id == user_id)) or 0


# ---- 등급 ----


def test_automation_tool_levels() -> None:
    """명세 도구 목록: list_*는 READ, create·delete는 INTERNAL. 자동 작업(예약·감시)에는 READ만 간다."""
    names = {
        origin: {name for toolset in TOOLSETS_BY_ORIGIN[origin] for name in toolset.tools} for origin in TaskOrigin
    }
    assert {"list_schedules", "list_watches"} <= names[TaskOrigin.SCHEDULE]
    assert not names[TaskOrigin.SCHEDULE] & {"create_schedule", "delete_schedule", "create_watch", "delete_watch"}
    chat_internal = {name for name in TOOLSETS_BY_ORIGIN[TaskOrigin.CHAT][1].tools}
    assert {"create_schedule", "delete_schedule", "create_watch", "delete_watch"} <= chat_internal


# ---- 테스트 10: 감시 제한 ----


@pytest.mark.parametrize("tool_name", ["add_todo", "remember", "create_event", "delete_event", "create_watch"])
async def test_watch_rejects_non_read_tools(tool_deps: AgentDeps, db_session: AsyncSession, tool_name: str) -> None:
    result = await create_watch(tool_deps, tool_name=tool_name, tool_input={})

    assert result.ok is False
    assert await count(db_session, Watch, tool_deps.user_id) == 0


@pytest.mark.parametrize("tool_name", ["list_mail", "read_mail"])
async def test_watch_rejects_mail_tools(tool_deps: AgentDeps, db_session: AsyncSession, tool_name: str) -> None:
    result = await create_watch(tool_deps, tool_name=tool_name, tool_input={"query": "택배"})

    assert result.ok is False
    assert "메일" in (result.error or "")
    assert await count(db_session, Watch, tool_deps.user_id) == 0


@pytest.mark.parametrize("minutes", [0, 5, 29])
async def test_watch_rejects_interval_under_30_minutes(
    tool_deps: AgentDeps, db_session: AsyncSession, minutes: int
) -> None:
    result = await create_watch(tool_deps, interval_minutes=minutes)

    assert result.ok is False
    assert "30분" in (result.error or "")
    assert await count(db_session, Watch, tool_deps.user_id) == 0


async def test_model_cannot_create_watch_under_30_minutes(
    client: httpx.AsyncClient, agent: MyAgent, conversation: int, db_session: AsyncSession, logged_in_user: Any
) -> None:
    """모델이 5분 간격으로 부르면 인자 검증에서 막혀 도구가 실행되지 않는다(eval 25번 상황)."""
    bad = ToolCallPart(
        "create_watch",
        {
            "description": "날씨",
            "tool_name": "get_weather",
            "tool_input": {"place": "서울", "date": TOMORROW},
            "condition_text": "비",
            "interval_minutes": 5,
        },
    )
    model = scripted(bad, text("감시 간격은 30분 이상이어야 합니다."))

    with agent.override(model=model.model()):
        response = await client.post(f"/api/conversations/{conversation}/messages", json=message_body("5분마다 확인"))

    assert response.status_code == 202
    assert await count(db_session, Watch, logged_in_user.id) == 0


async def test_watch_rejects_bad_tool_input(tool_deps: AgentDeps, db_session: AsyncSession) -> None:
    result = await create_watch(tool_deps, tool_input={"place": "서울", "date": "내일"})

    assert result.ok is False
    assert await count(db_session, Watch, tool_deps.user_id) == 0


async def test_watch_created_with_30_day_expiry(tool_deps: AgentDeps, db_session: AsyncSession) -> None:
    """날짜 인자가 없는 감시는 30일 뒤 만료(날짜가 있으면 그 날이 끝날 때, tests/test_watch_conditions.py)."""
    before = datetime.now(UTC)

    result = await create_watch(
        tool_deps, interval_minutes=30, repeat=True, tool_name="list_todos", tool_input={}, condition_text="할 일 있음"
    )

    assert result.ok is True, result.error
    data = payload(result)
    watch = await db_session.get_one(Watch, data["id"])
    assert (watch.tool_name, watch.interval_minutes, watch.repeat, watch.is_active) == ("list_todos", 30, True, True)
    assert watch.tool_input == {}
    assert timedelta(days=29, hours=23) < watch.expires_at - before < timedelta(days=30, minutes=5)
    assert watch.next_run_at <= datetime.now(UTC) + timedelta(minutes=1)  # 첫 확인은 다음 검사 때
    assert data["interval_minutes"] == 30 and data["expires_on"]
    # 2026-11-19(46번): 첫 확인 시각을 요일까지 알려 준다("다음 검사 때"만으로는 언제인지 알 수 없다).
    local = watch.next_run_at.astimezone(rules.SEOUL)
    weekday = "월화수목금토일"[local.weekday()]
    assert data["next_check"] == f"{local:%Y-%m-%d}({weekday}) {local:%H:%M}"


async def test_duplicate_watch_is_rejected(tool_deps: AgentDeps, db_session: AsyncSession) -> None:
    """Django판에서 같은 감시를 두 번 만든 버그. 같은 도구·입력·조건(공백 무시)이면 서버가 거절한다."""
    assert (await create_watch(tool_deps)).ok

    again = await create_watch(tool_deps, condition_text="  강수 확률이   60%를 넘는다 ")

    assert again.ok is False
    assert "이미" in (again.error or "")
    assert await count(db_session, Watch, tool_deps.user_id) == 1


async def test_active_watch_limit_is_10(tool_deps: AgentDeps, db_session: AsyncSession) -> None:
    for number in range(rules.MAX_ACTIVE_WATCHES):
        assert (await create_watch(tool_deps, condition_text=f"조건 {number}")).ok

    result = await create_watch(tool_deps, condition_text="열한 번째")

    assert result.ok is False
    assert "10개" in (result.error or "")
    assert await count(db_session, Watch, tool_deps.user_id) == rules.MAX_ACTIVE_WATCHES


# ---- 예약 제한 ----


async def test_create_schedule_computes_next_run(tool_deps: AgentDeps, db_session: AsyncSession) -> None:
    result = await create_schedule(tool_deps)

    assert result.ok is True, result.error
    job = await db_session.get_one(ScheduledJob, payload(result)["id"])
    assert job.weekdays == [6]
    local = job.next_run_at.astimezone(rules.SEOUL)
    assert (local.weekday(), local.hour, local.minute) == (6, 21, 0)
    assert job.next_run_at > datetime.now(UTC)
    assert payload(result)["repeat_text"] == "매주 일요일 21:00"
    # 2026-11-19(46번): 다음 실행을 요일까지 붙여 준다(모델이 요일을 직접 계산하지 않게).
    assert payload(result)["next_run"] == f"{local:%Y-%m-%d}(일) 21:00"


@pytest.mark.parametrize(
    ("time_of_day", "weekdays"),
    [("25:00", [0]), ("9시", [0]), ("09:00", []), ("09:00", [7]), ("09:00", [-1])],
)
async def test_schedule_rejects_bad_time_or_weekdays(
    tool_deps: AgentDeps, db_session: AsyncSession, time_of_day: str, weekdays: list[int]
) -> None:
    result = await create_schedule(tool_deps, time_of_day=time_of_day, weekdays=weekdays)

    assert result.ok is False
    assert await count(db_session, ScheduledJob, tool_deps.user_id) == 0


async def test_duplicate_schedule_is_rejected(tool_deps: AgentDeps, db_session: AsyncSession) -> None:
    assert (await create_schedule(tool_deps, weekdays=[6, 0])).ok

    again = await create_schedule(tool_deps, weekdays=[0, 6, 6], prompt_text="다음 주   일정을 요약해줘")

    assert again.ok is False
    assert "이미" in (again.error or "")
    assert await count(db_session, ScheduledJob, tool_deps.user_id) == 1


async def test_active_schedule_limit_is_10(tool_deps: AgentDeps, db_session: AsyncSession) -> None:
    for number in range(rules.MAX_ACTIVE_SCHEDULES):
        assert (await create_schedule(tool_deps, prompt_text=f"요약 {number}")).ok

    result = await create_schedule(tool_deps, prompt_text="열한 번째")

    assert result.ok is False
    assert "10개" in (result.error or "")


# ---- 조회·삭제 ----


async def test_list_and_delete_only_my_automations(
    tool_deps: AgentDeps, db_session: AsyncSession, make_user: Any
) -> None:
    other = await make_user("other@example.com", "password-other")
    other_job = ScheduledJob(
        user_id=other.id,
        title="남의 예약",
        prompt_text="x",
        time_of_day=datetime(2026, 1, 1, 9, 0).time(),
        weekdays=[0],
        next_run_at=datetime.now(UTC),
    )
    db_session.add(other_job)
    await db_session.commit()
    mine = payload(await create_schedule(tool_deps))["id"]
    watch_id = payload(await create_watch(tool_deps))["id"]

    schedules = payload(await tools.list_schedules(tool_context(tool_deps, "list_schedules")))["schedules"]
    watches = payload(await tools.list_watches(tool_context(tool_deps, "list_watches")))["watches"]
    denied = await tools.delete_schedule(tool_context(tool_deps, "delete_schedule"), schedule_id=other_job.id)
    deleted = await tools.delete_schedule(tool_context(tool_deps, "delete_schedule"), schedule_id=mine)
    deleted_watch = await tools.delete_watch(tool_context(tool_deps, "delete_watch"), watch_id=watch_id)

    assert [s["id"] for s in schedules] == [mine]
    assert [w["id"] for w in watches] == [watch_id]
    assert denied.ok is False
    assert deleted.ok is True and deleted_watch.ok is True
    assert await db_session.get(ScheduledJob, other_job.id) is not None
    assert await count(db_session, ScheduledJob, tool_deps.user_id) == 0
    assert await count(db_session, Watch, tool_deps.user_id) == 0


async def test_coupang_price_watch_no_longer_exists(tool_deps: AgentDeps, db_session: AsyncSession) -> None:
    """2026-11-19 사용자 결정으로 쇼핑 기능(쿠팡 비교·목표가 감시)을 지웠다. 그 도구 이름으로 감시를 만들 수 없다."""
    result = await create_watch(
        tool_deps, tool_name="coupang_price", tool_input={"product": "KB100"}, condition_text="10만 원 이하"
    )

    assert result.ok is False
    assert "조회(READ) 도구만" in (result.error or "")  # 감시 전용 도구가 아니라 없는 도구로 거절된다
    assert "coupang_price" not in (result.error or "")  # 쓸 수 있는 도구 목록에도 없다
    assert await count(db_session, Watch, tool_deps.user_id) == 0
