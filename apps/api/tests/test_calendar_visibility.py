"""Google 캘린더가 연결되지 않았으면 일정 쓰기 도구를 모델에게 보이지 않는다(2026-11-19 사용자 결정: 추천 제안 진행).

예전에는 미연결이어도 모델이 create_event를 불러 "승인해도 실패한다"는 경고가 붙은 승인 카드가 떴다
(DECISIONS의 "(제안) Google이 연결되지 않았으면" 줄).
연결 여부는 run을 새로 시작할 때 한 번만 본다(run 도중 도구 목록이 바뀌면 프롬프트 캐시가 깨진다).
승인 뒤 재개하는 run은 보지 않는다(승인한 호출이 도구를 못 찾으면 안 된다).
"""

import httpx
from pydantic_ai.messages import ModelRequest

from app.agent.agent import MyAgent
from app.agent.deps import AgentDeps
from app.agent.prompts import CALENDAR_NOT_CONNECTED
from app.google_auth import delete_connection
from tests.fake_http import FakeHttp, fixture_response
from tests.model_scripts import call, scripted, text
from tests.test_agent_run import send
from tests.test_approvals import approve
from tests.test_gcal import connect_google

WRITE_TOOLS = {"create_event", "update_event", "delete_event"}
EVENTS = "https://www.googleapis.com/calendar/v3/calendars/primary/events"


def instructions_seen(model) -> str:
    requests = [m for m in model.seen_messages[0] if isinstance(m, ModelRequest)]
    return requests[-1].instructions or ""


async def test_unconnected_chat_hides_calendar_writes(
    client: httpx.AsyncClient, agent: MyAgent, conversation: int
) -> None:
    model = scripted(text("설정에서 Google을 연결해 주세요."))
    with agent.override(model=model.model()):
        await send(client, conversation, "내일 3시에 치과 일정 넣어줘")

    [names] = model.seen_tool_names
    assert not WRITE_TOOLS & set(names)
    assert "list_events" in names  # 조회는 남겨 "연결해 주세요" 안내를 받을 수 있게
    assert CALENDAR_NOT_CONNECTED in instructions_seen(model)


async def test_connected_chat_shows_calendar_writes(
    client: httpx.AsyncClient, agent: MyAgent, conversation: int, tool_deps: AgentDeps
) -> None:
    await connect_google(tool_deps)
    model = scripted(text("네"))
    with agent.override(model=model.model()):
        await send(client, conversation, "안녕")

    [names] = model.seen_tool_names
    assert set(names) >= WRITE_TOOLS
    assert CALENDAR_NOT_CONNECTED not in instructions_seen(model)


async def test_resume_keeps_write_tools_after_disconnect(
    client: httpx.AsyncClient, agent: MyAgent, conversation: int, tool_deps: AgentDeps, fake_http: FakeHttp
) -> None:
    await connect_google(tool_deps)
    fake_http.route("GET", f"{EVENTS}/evt0meeting001", fixture_response("google_event_meeting.json"))
    fake_http.route("DELETE", EVENTS, httpx.Response(204))
    model = scripted(call("delete_event", event_id="evt0meeting001"), text("지웠습니다."))
    with agent.override(model=model.model()):
        task = await send(client, conversation, "팀 회의 지워줘")
        async with tool_deps.db() as session:
            await delete_connection(session, tool_deps.user_id)
            await session.commit()
        await approve(client, task["approvals"][0]["id"])

    assert "delete_event" in model.seen_tool_names[-1]  # 재개한 run도 승인한 도구를 찾을 수 있다
