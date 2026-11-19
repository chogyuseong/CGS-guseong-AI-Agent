"""일정 수정·삭제 대상이 모호하면 승인 카드를 만들지 않고 되묻는다(2026-11-19, eval 07 회귀 대응).

"치과 일정 5시로 옮겨줘"에 '치과'(11/19)와 '치과 정기검진'(11/26)이 있으면 모델이 하나를 골라 승인을 요청하는 일이
4~5회 중 2회 있었다(프롬프트 보강으로도 그대로). 서버가 확정적으로 막는다: 이번 작업의 일정 조회 결과에 제목 낱말이
겹치는 후보가 둘 이상인데 사용자 메시지에 그 일정을 집어낼 단서(날짜·내일·시각·그 일정에만 있는 제목 낱말)가 없으면
카드 대신 후보를 보여 주며 묻는다. 가짜 모델·가짜 Google만 쓴다.
"""

from datetime import date

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent import ambiguity
from app.agent.agent import MyAgent
from app.agent.deps import AgentDeps
from app.models import UsageLog
from tests.fake_http import FakeHttp, json_response
from tests.model_scripts import call, scripted, text
from tests.test_agent_run import send
from tests.test_gcal import connect_google

TODAY = date(2026, 11, 18)
DENTIST = {"id": "evtdentist1", "title": "치과", "start": "2026-11-19T15:00:00+09:00"}
CHECKUP = {"id": "evtdentist2", "title": "치과 정기검진", "start": "2026-11-26T10:00:00+09:00"}
LUNCH = {"id": "evtlunch", "title": "점심 약속", "start": "2026-11-19T12:00:00+09:00"}
EVENTS = [DENTIST, CHECKUP, LUNCH]


def test_candidates_share_a_title_word():
    assert [e["id"] for e in ambiguity.candidates(EVENTS, DENTIST)] == ["evtdentist1", "evtdentist2"]
    assert [e["id"] for e in ambiguity.candidates(EVENTS, LUNCH)] == ["evtlunch"]


@pytest.mark.parametrize(
    ("message", "target", "pinned"),
    [
        ("치과 일정 5시로 옮겨줘", DENTIST, False),  # eval 07: 단서 없음
        ("19일 치과 5시로 옮겨줘", DENTIST, True),  # 날짜
        ("11월 19일 치과 5시로", DENTIST, True),
        ("내일 치과 5시로 옮겨줘", DENTIST, True),  # 오늘(11/18) 기준 내일 = 11/19
        ("3시 치과 5시로 옮겨줘", DENTIST, True),  # 그 일정의 시작 시각
        ("목요일 치과 5시로 옮겨줘", DENTIST, False),  # 두 일정 모두 목요일이라 단서가 아니다
        ("정기검진 5시로 옮겨줘", CHECKUP, True),  # 그 일정에만 있는 제목 낱말
        ("정기검진 5시로 옮겨줘", DENTIST, False),  # 다른 후보의 낱말을 말했는데 이 일정을 골랐다
    ],
)
def test_pinned_needs_a_distinguishing_clue(message, target, pinned):
    others = [e for e in ambiguity.candidates(EVENTS, target) if e is not target]
    assert ambiguity.pinned(message, target, others, TODAY) is pinned


def calendar(fake_http: FakeHttp) -> None:
    items = [
        {"id": e["id"], "summary": e["title"], "start": {"dateTime": e["start"]}, "end": {"dateTime": e["start"]}}
        for e in EVENTS
    ]
    fake_http.route(
        "GET", "https://www.googleapis.com/calendar/v3/calendars/primary/events", json_response(200, {"items": items})
    )


async def test_ambiguous_update_asks_instead_of_approval_card(
    client: httpx.AsyncClient,
    agent: MyAgent,
    conversation: int,
    tool_deps: AgentDeps,
    fake_http: FakeHttp,
    db_session: AsyncSession,
    monkeypatch,
) -> None:
    monkeypatch.setattr(ambiguity, "today_seoul", lambda: TODAY)
    await connect_google(tool_deps)
    calendar(fake_http)
    model = scripted(
        call("list_events", date_from="2026-11-18", date_to="2026-11-30"),
        call("update_event", event_id="evtdentist1", start="2026-11-19T17:00", end="2026-11-19T18:00"),
        text("옮겼습니다."),
    )
    with agent.override(model=model.model()):
        task = await send(client, conversation, "치과 일정 5시로 옮겨줘")

    assert task["approvals"] == []
    assert task["status"] == "done"
    assert "치과 정기검진" in task["final_answer"] and "11월 26일" in task["final_answer"]
    assert "어느 일정" in task["final_answer"]
    assert fake_http.sent("PATCH", "https://www.googleapis.com") == []
    models = await db_session.scalars(select(UsageLog.model).where(UsageLog.task_id == task["id"]))
    assert "unknown" not in list(models)  # 서버가 만든 되묻기 문장은 비용 기록에 들어가지 않는다


async def test_pinned_update_still_asks_for_approval(
    client: httpx.AsyncClient, agent: MyAgent, conversation: int, tool_deps: AgentDeps, fake_http: FakeHttp, monkeypatch
) -> None:
    monkeypatch.setattr(ambiguity, "today_seoul", lambda: TODAY)
    await connect_google(tool_deps)
    calendar(fake_http)
    fake_http.route(
        "GET",
        "https://www.googleapis.com/calendar/v3/calendars/primary/events/evtdentist1",
        json_response(
            200,
            {
                "id": "evtdentist1",
                "summary": "치과",
                "start": {"dateTime": DENTIST["start"]},
                "end": {"dateTime": DENTIST["start"]},
                "etag": '"1"',
            },
        ),
    )
    model = scripted(
        call("list_events", date_from="2026-11-18", date_to="2026-11-30"),
        call("update_event", event_id="evtdentist1", start="2026-11-19T17:00", end="2026-11-19T18:00"),
        text("승인을 기다립니다."),
    )
    with agent.override(model=model.model()):
        task = await send(client, conversation, "내일 치과 5시로 옮겨줘")

    assert task["status"] == "waiting_approval"
    assert [a["tool_name"] for a in task["approvals"]] == ["update_event"]


def calendar_by_range(fake_http: FakeHttp) -> None:
    """조회 기간(timeMin~timeMax) 안의 일정만 돌려주는 가짜 Google(실제 API처럼)."""

    def reply(request: httpx.Request) -> httpx.Response:
        low, high = request.url.params.get("timeMin", ""), request.url.params.get("timeMax", "9999")
        items = [
            {"id": e["id"], "summary": e["title"], "start": {"dateTime": e["start"]}, "end": {"dateTime": e["start"]}}
            for e in EVENTS
            if low[:10] <= e["start"][:10] < high[:10]
        ]
        return json_response(200, {"items": items})

    fake_http.route("GET", "https://www.googleapis.com/calendar/v3/calendars/primary/events", reply)


async def test_narrow_lookup_does_not_hide_other_candidates(
    client: httpx.AsyncClient, agent: MyAgent, conversation: int, tool_deps: AgentDeps, fake_http: FakeHttp, monkeypatch
) -> None:
    """eval 07 재발(2026-11-19): 모델이 일주일(11/18~11/24)만 조회해 11/26 '치과 정기검진'을 못 봤다.
    서버가 넓은 범위를 직접 한 번 더 조회해 후보를 찾는다."""
    monkeypatch.setattr(ambiguity, "today_seoul", lambda: TODAY)
    await connect_google(tool_deps)
    calendar_by_range(fake_http)
    model = scripted(
        call("list_events", date_from="2026-11-18", date_to="2026-11-24"),
        call("update_event", event_id="evtdentist1", start="2026-11-19T17:00", end="2026-11-19T18:00"),
        text("옮겼습니다."),
    )
    with agent.override(model=model.model()):
        task = await send(client, conversation, "치과 일정 5시로 옮겨줘")

    assert task["approvals"] == []
    assert "치과 정기검진" in task["final_answer"]
