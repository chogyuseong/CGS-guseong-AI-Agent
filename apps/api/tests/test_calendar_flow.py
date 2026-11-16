"""SPEC '요청 한 건 따라가기': "이번 주 금요일 저녁 7시에 성수동에서 저녁 약속 잡아줘".

FunctionModel 대본으로 search_places(READ) → create_event(EXTERNAL) → 승인 대기 → 승인
→ 가짜 Google 응답으로 등록 → done.
거절하면 Google에 아무 요청도 보내지 않는다. 실제 모델·카카오·Google은 부르지 않는다.
"""

from datetime import date

import httpx
from pydantic_ai.messages import ModelRequest, ToolReturnPart, UserPromptPart

from app.agent.agent import MyAgent
from app.agent.guard import GUARD_INSTRUCTIONS, GuardAgent
from app.agent.prompts import date_instruction
from app.agent.toolsets import TOOLSETS_BY_ORIGIN
from app.models.enums import TaskOrigin
from tests.fake_http import FakeHttp, fixture_response
from tests.model_scripts import GuardScript, call, scripted, text
from tests.test_agent_run import send
from tests.test_approvals import approve, reject, task_detail
from tests.test_gcal import connect_google
from tests.test_places import KAKAO, by_query

EVENTS = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
REQUEST = "이번 주 금요일 저녁 7시에 성수동에서 저녁 약속 잡아줘"
PREVIEW = "11월 20일(금) 19:00~21:00 '저녁 약속' 일정을 기본 캘린더에 만듭니다. 장소: 성수동."


def dinner_script():
    return scripted(
        call("search_places", query="저녁 식사", near="성수동"),
        call("create_event", title="저녁 약속", start="2026-11-20T19:00", end="2026-11-20T21:00", location="성수동"),
        text("11월 20일(금) 저녁 7시로 구글 캘린더에 등록했습니다."),
    )


async def test_search_then_create_waits_for_approval_and_runs_once(
    client: httpx.AsyncClient, agent: MyAgent, guard: GuardAgent, conversation: int, fake_http: FakeHttp, tool_deps
) -> None:
    await connect_google(tool_deps)
    fake_http.route("GET", KAKAO, by_query)
    fake_http.route("POST", EVENTS, fixture_response("google_event_created.json"))
    guard_model = GuardScript()

    with agent.override(model=dinner_script().model()), guard.override(model=guard_model.model()):
        task = await send(client, conversation, REQUEST)
        waiting_posts = len(fake_http.sent("POST", EVENTS))
        response = await approve(client, task["approvals"][0]["id"])

    assert waiting_posts == 0  # 승인 전에는 Google에 아무것도 보내지 않는다
    assert task["status"] == "waiting_approval"
    assert [(s["tool_name"], s["status"]) for s in task["steps"]] == [
        ("search_places", "done"),
        ("create_event", "waiting_approval"),
    ]
    assert task["steps"][0]["label"] == "카카오맵에서 성수동 저녁 식사 찾는 중"
    [approval] = task["approvals"]
    assert approval["summary"] == PREVIEW
    assert approval["guard"]["verdict"] == "match"
    assert response.status_code == 200, response.text
    assert len(fake_http.sent("POST", EVENTS)) == 1
    detail = await task_detail(client, task["id"])
    assert detail["status"] == "done"
    assert detail["final_answer"] == "11월 20일(금) 저녁 7시로 구글 캘린더에 등록했습니다."
    assert (detail["steps"][1]["status"], detail["steps"][1]["ok"]) == ("done", True)
    assert detail["steps"][1]["output"]["data"]["saved_to"] == "Google 캘린더"


async def test_rejected_create_never_calls_google(
    client: httpx.AsyncClient, agent: MyAgent, conversation: int, fake_http: FakeHttp, tool_deps
) -> None:
    await connect_google(tool_deps)
    fake_http.route("GET", KAKAO, by_query)
    model = scripted(
        call("search_places", query="저녁 식사", near="성수동"),
        call("create_event", title="저녁 약속", start="2026-11-20T19:00", end="2026-11-20T21:00"),
        text("일정은 등록하지 않았습니다."),
    )

    with agent.override(model=model.model()):
        task = await send(client, conversation, REQUEST)
        await reject(client, task["approvals"][0]["id"])

    assert fake_http.sent("POST", EVENTS) == []
    returns = [p for m in model.seen_messages[-1] for p in m.parts if isinstance(p, ToolReturnPart)]
    assert returns[-1].content == "사용자가 거절함"
    detail = await task_detail(client, task["id"])
    assert detail["status"] == "done"
    assert detail["steps"][1]["status"] == "rejected"


async def test_guard_sees_preview_but_not_place_results(
    client: httpx.AsyncClient, agent: MyAgent, guard: GuardAgent, conversation: int, fake_http: FakeHttp, tool_deps
) -> None:
    await connect_google(tool_deps)
    fake_http.route("GET", KAKAO, by_query)
    guard_model = GuardScript()

    with agent.override(model=dinner_script().model()), guard.override(model=guard_model.model()):
        await send(client, conversation, REQUEST)

    [messages] = guard_model.seen_messages
    prompt = " ".join(
        str(p.content)
        for m in messages
        if isinstance(m, ModelRequest)
        for p in m.parts
        if isinstance(p, UserPromptPart)
    )
    assert PREVIEW in prompt
    assert REQUEST in prompt
    assert "성수베이킹스튜디오" not in prompt  # 검색 결과(도구 결과)는 감시 에이전트에 넣지 않는다


def test_guard_instructions_mark_quoted_text_as_data() -> None:
    assert "「」" in GUARD_INSTRUCTIONS
    assert "데이터" in GUARD_INSTRUCTIONS


def test_calendar_write_tools_are_only_in_chat_toolsets() -> None:
    """예약·감시 작업은 READ만 받는다(절대 원칙 4). 일정 쓰기 도구는 chat에만 있다."""
    for origin in (TaskOrigin.SCHEDULE, TaskOrigin.WATCH):
        names = {name for toolset in TOOLSETS_BY_ORIGIN[origin] for name in toolset.tools}
        assert "list_events" in names
        assert not names & {"create_event", "update_event", "delete_event"}


def test_date_instruction_lists_this_week() -> None:
    """'이번 주 금요일' 같은 말을 모델이 실제 날짜로 바꿀 수 있게 이번 주 날짜를 지시문에 준다."""
    instruction = date_instruction(date(2026, 11, 17))

    assert instruction.startswith("오늘은 2026년 11월 17일 화요일이다(Asia/Seoul).")
    assert "11월 20일(금)" in instruction
    assert "11월 16일(월)" in instruction and "11월 22일(일)" in instruction
    assert "다음 주 월요일은 11월 23일" in instruction


def test_date_instruction_across_month_end() -> None:
    instruction = date_instruction(date(2026, 12, 31))

    assert "2027년 1월 3일(일)" in instruction
    assert "다음 주 월요일은 2027년 1월 4일" in instruction
