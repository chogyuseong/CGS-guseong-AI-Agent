"""한 번의 승인 요청에서 일정 삭제(delete_event)는 1건까지만(2026-11-19 사용자 결정: 추천 제안 진행).

eval 08번("이번 달 일정 전부 지워줘")에서 모델이 삭제 승인 카드 3장을 한꺼번에 띄운 일이 있었다(docs/EVALS.md).
프롬프트로 "먼저 확인"을 시켰지만, 모델이 어겨도 서버가 대량 삭제 카드를 만들지 않게 막는다.
가짜 모델·가짜 Google만 쓴다.
"""

import httpx
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo

from app.agent.agent import MyAgent
from app.agent.deps import AgentDeps
from app.agent.runner import BULK_DELETE_MESSAGE
from tests.fake_http import FakeHttp, fixture_response
from tests.model_scripts import current_run_messages, registered
from tests.test_agent_run import send
from tests.test_gcal import connect_google

EVENTS = "https://www.googleapis.com/calendar/v3/calendars/primary/events"


def two_tools_at_once(*names_and_ids: tuple[str, dict]):
    """첫 응답에서 여러 도구를 한꺼번에 부르고, 그 뒤에는 글로 답하는 가짜 모델."""

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if not any(isinstance(m, ModelResponse) for m in current_run_messages(messages)):
            calls = [ToolCallPart(name, args, tool_call_id=f"call-{i}") for i, (name, args) in enumerate(names_and_ids)]
            return ModelResponse(parts=list(calls))
        return ModelResponse(parts=[TextPart("처리했습니다.")])

    return registered(respond)


def google_events(fake_http: FakeHttp) -> None:
    fake_http.route("GET", EVENTS, fixture_response("google_event_meeting.json"))


async def test_two_deletes_at_once_make_no_approval_cards(
    client: httpx.AsyncClient, agent: MyAgent, conversation: int, fake_http: FakeHttp, tool_deps: AgentDeps
) -> None:
    await connect_google(tool_deps)
    google_events(fake_http)
    model = two_tools_at_once(
        ("delete_event", {"event_id": "evtA0000001"}), ("delete_event", {"event_id": "evtB0000002"})
    )

    with agent.override(model=model):
        task = await send(client, conversation, "이번 달 일정 전부 지워줘")

    assert task["approvals"] == []
    assert task["status"] == "failed"
    assert task["error"] == BULK_DELETE_MESSAGE
    assert fake_http.sent("DELETE", EVENTS) == []


async def test_single_delete_still_asks_for_approval(
    client: httpx.AsyncClient, agent: MyAgent, conversation: int, fake_http: FakeHttp, tool_deps: AgentDeps
) -> None:
    await connect_google(tool_deps)
    google_events(fake_http)
    model = two_tools_at_once(("delete_event", {"event_id": "evt0meeting001"}))

    with agent.override(model=model):
        task = await send(client, conversation, "팀 회의 일정 지워줘")

    assert task["status"] == "waiting_approval"
    assert [a["tool_name"] for a in task["approvals"]] == ["delete_event"]
