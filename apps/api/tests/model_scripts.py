"""테스트용 가짜 모델(FunctionModel) 도우미. 실제 LLM은 부르지 않는다.

FunctionModel은 "메시지 목록을 받아 모델 응답을 돌려주는 함수"로 모델을 흉내 낸다.
여기서는 대본(script)처럼 응답을 차례대로 돌려주고, 모델이 받은 도구 목록과 메시지를 기록해 둔다.

3단계(DBOS): 워크플로 안에서는 DBOSDurability에 등록된 모델 객체만 agent.override(model=...)로 끼울 수 있다
(모델 객체는 단계 경계를 넘어 직렬화할 수 없어서). 그래서 앱을 만들 때 SCRIPTED_MODEL 하나를 등록해 두고
(TEST_AGENT_MODELS → create_app(agent_models=...)), 테스트마다 그 모델이 부를 함수만 바꿔 끼운다(registered).
"""

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from uuid import uuid4

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ModelResponsePart,
    TextPart,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

type ModelFunction = Callable[[list[ModelMessage], AgentInfo], ModelResponse | Awaitable[ModelResponse]]


class ModelSlot:
    """등록된 가짜 모델 하나가 지금 부를 함수를 담아 두는 자리."""

    def __init__(self) -> None:
        self.current: ModelFunction | None = None

    async def respond(self, messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        assert self.current is not None, "가짜 모델 대본이 없다"
        result = self.current(messages, info)
        return await result if inspect.isawaitable(result) else result


MODEL_SLOT = ModelSlot()
SCRIPTED_MODEL = FunctionModel(MODEL_SLOT.respond, model_name="scripted")
TEST_AGENT_MODELS = {"scripted": SCRIPTED_MODEL}


def registered(function: ModelFunction) -> FunctionModel:
    """function을 부르는 등록된 가짜 모델을 돌려준다. 워크플로 안에서도 agent.override(model=...)에 쓸 수 있다."""
    MODEL_SLOT.current = function
    return SCRIPTED_MODEL


def current_run_messages(messages: list[ModelMessage]) -> list[ModelMessage]:
    """이전 task에서 넘어온 기록을 빼고, 마지막 사용자 요청부터의 메시지만 돌려준다."""
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if isinstance(message, ModelRequest) and any(isinstance(p, UserPromptPart) for p in message.parts):
            return messages[index:]
    return messages


@dataclass
class ScriptedModel:
    """응답 대본을 차례대로 돌려주는 가짜 모델. 대본이 끝나면 마지막 응답을 반복한다."""

    script: list[ModelResponsePart]
    calls: int = 0
    seen_tool_names: list[list[str]] = field(default_factory=list)
    seen_messages: list[list[ModelMessage]] = field(default_factory=list)

    def respond(self, messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        self.calls += 1
        self.seen_tool_names.append(sorted(tool.name for tool in info.function_tools))
        self.seen_messages.append(list(messages))
        responses_so_far = sum(isinstance(m, ModelResponse) for m in current_run_messages(messages))
        part = self.script[min(responses_so_far, len(self.script) - 1)]
        if isinstance(part, ToolCallPart):
            # 실제 모델처럼 호출마다 새 tool_call_id를 붙인다(같은 대본 줄을 반복해도 다른 호출이 되게).
            part = replace(part, tool_call_id=f"call-{uuid4().hex[:12]}")
        return ModelResponse(parts=[part])

    def model(self) -> FunctionModel:
        return registered(self.respond)


def scripted(*parts: ModelResponsePart) -> ScriptedModel:
    return ScriptedModel(script=list(parts))


@dataclass
class GuardScript:
    """감시 에이전트용 가짜 모델. 정해 둔 판정을 돌려주고, 감시 에이전트가 받은 메시지를 기록한다."""

    verdict: str = "match"
    reason: str = "요청과 일치합니다."
    seen_messages: list[list[ModelMessage]] = field(default_factory=list)

    def respond(self, messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        self.seen_messages.append(list(messages))
        output_tool = info.output_tools[0].name
        return ModelResponse(parts=[ToolCallPart(output_tool, {"verdict": self.verdict, "reason": self.reason})])

    def model(self) -> FunctionModel:
        return FunctionModel(self.respond, model_name="guard-scripted")


def call(tool_name: str, **args: object) -> ToolCallPart:
    return ToolCallPart(tool_name, dict(args))


def text(content: str) -> TextPart:
    return TextPart(content)
