"""9단계 보강: 모델 API 장애 때 화면에 남는 것(docs/OPERATIONS.md "장애 대응").

실제 장애를 일으키지 않고, 등록된 가짜 모델이 제공자 오류(ModelHTTPError 529 과부하)를 던지게 한다.
기대: task는 failed, 채팅에 고정된 한국어 오류 문장 한 줄, 제공자 응답 본문·내부 오류 내용은 화면에 나가지 않는다.
"""

import httpx
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models.function import AgentInfo

from app.agent import runner
from app.agent.agent import MyAgent
from tests.model_scripts import registered
from tests.test_agent_run import send

PROVIDER_BODY = {"type": "error", "error": {"type": "overloaded_error", "message": "PROVIDER-INTERNAL-DETAIL"}}


def overloaded(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    raise ModelHTTPError(status_code=529, model_name="scripted", body=PROVIDER_BODY)


async def test_model_provider_outage_fails_task_with_fixed_message(
    client: httpx.AsyncClient, agent: MyAgent, conversation: int
) -> None:
    with agent.override(model=registered(overloaded)):
        task = await send(client, conversation, "오늘 할 일 알려줘")

    assert task["status"] == "failed"
    assert task["error"] == runner.UNEXPECTED_MESSAGE
    messages = (await client.get(f"/api/conversations/{conversation}/messages")).json()
    assert messages[-1]["role"] == "assistant"
    assert messages[-1]["content"] == runner.UNEXPECTED_MESSAGE
    assert "PROVIDER-INTERNAL-DETAIL" not in str(task) + str(messages)
