"""4단계 보강(4): draft_message (BUILD_SPEC 7.1, 7.4). INTERNAL, 초안만 돌려주고 보내지 않는다.

- 받는 사람(표시용 이름)과 본문을 나눠 돌려준다. sent=false와 "보내지 않았다"는 안내를 함께 준다.
- 외부 서비스로 아무것도 보내지 않는다(HTTP 요청 0). 승인도 필요 없다(앱 밖에 흔적을 남기지 않는다).
- 본문은 모델이 쓴다(추가 모델 호출 없음 → 따로 잡을 예산 없음).
"""

import httpx
import pytest
from pydantic import ValidationError

from app.agent.agent import MyAgent
from app.agent.deps import AgentDeps
from app.agent.results import ToolStatus
from app.agent.toolsets import TOOLSETS_BY_ORIGIN, internal_tools, label_for
from app.models.enums import TaskOrigin
from app.tools import drafts
from tests.conftest import payload, tool_context
from tests.fake_http import FakeHttp
from tests.model_scripts import call, scripted, text
from tests.test_agent_run import send

BODY = "민수야, 내일 저녁 7시에 성수동에서 보자. 늦으면 연락 줘."


async def test_draft_returns_recipient_and_body_without_sending(tool_deps: AgentDeps, fake_http: FakeHttp) -> None:
    result = await drafts.draft_message(tool_context(tool_deps, "draft_message"), recipient="민수", body=BODY)

    data = payload(result)
    assert result.status == ToolStatus.SUCCEEDED
    assert data["recipient"] == "민수"
    assert data["body"] == BODY
    assert data["sent"] is False
    assert "보내지 않았습니다" in data["notice"]
    assert fake_http.requests == []


@pytest.mark.parametrize("bad", [{"recipient": "", "body": BODY}, {"recipient": "민수", "body": ""}])
def test_draft_arguments_are_validated(bad: dict) -> None:
    tool = internal_tools.tools["draft_message"]
    with pytest.raises(ValidationError):
        tool.function_schema.validator.validate_python(bad)


def test_draft_is_internal_chat_only_with_label() -> None:
    assert "draft_message" in internal_tools.tools
    for origin in (TaskOrigin.SCHEDULE, TaskOrigin.WATCH):
        assert all("draft_message" not in toolset.tools for toolset in TOOLSETS_BY_ORIGIN[origin])
    assert label_for("draft_message", {"recipient": "민수"}) == "민수에게 보낼 초안 쓰는 중"


async def test_chat_draft_is_done_without_approval(
    client: httpx.AsyncClient, agent: MyAgent, conversation: int, fake_http: FakeHttp
) -> None:
    model = scripted(
        call("draft_message", recipient="민수", body=BODY), text("초안을 만들었습니다. 복사해서 보내 주세요.")
    )
    with agent.override(model=model.model()):
        task = await send(client, conversation, "민수한테 내일 7시 성수동 약속 문자 써줘")

    [step] = task["steps"]
    assert task["status"] == "done"
    assert task["approvals"] == []
    assert step["tool_name"] == "draft_message"
    assert step["risk_level"] == "INTERNAL"
    assert step["output"]["data"]["body"] == BODY
    assert fake_http.requests == []
