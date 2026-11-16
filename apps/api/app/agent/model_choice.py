"""모델 선택: FAST로 시작하고, 이번 task의 도구 호출이 4번을 넘으면 SMART로 이어 간다.

구현 방법 (Pydantic AI 2.68 공식 기능만 사용):
- SelectModel capability: 모델에 요청을 보내기 직전마다 selector를 불러 이번 단계의 모델을 고른다.
  selector는 지금까지의 메시지 목록을 받으므로, 이번 task에서 나온 도구 호출 수를 셀 수 있다.
- ResolveModelId capability: 'anthropic:모델ID' 문자열을 실제 모델 객체로 만든다. API 키를 os.environ이 아니라
  설정(.env를 읽은 Settings)에서 넘기기 위해 필요하다.
- 둘 다 Agent를 만들 때 넣는 capability이고 모델을 "문자열"로 고르므로, 3단계의 DBOSDurability와 충돌하지 않는다
  (DBOS 문서: 모델 이름 문자열은 등록 없이 단계 안에서 다시 만들어진다. 실행 시점 capabilities 전달만 금지).
- 테스트에서 agent.override(model=FunctionModel)로 모델을 지정하면 이 선택은 건너뛴다(명시한 모델이 우선).
"""

from collections.abc import Callable
from typing import Any

from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, ToolCallPart, UserPromptPart
from pydantic_ai.models import Model, ModelResolutionContext, ModelSelectionContext
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.providers.anthropic import AnthropicProvider

from app.agent.deps import AgentDeps
from app.config import Settings

# 명세: "한 작업의 도구 호출이 4번을 넘으면 나머지는 SMART로 이어 간다."
SWITCH_AFTER_TOOL_CALLS = 4
ANTHROPIC_PREFIX = "anthropic:"


def count_tool_calls_in_current_run(messages: list[ModelMessage]) -> int:
    """마지막 사용자 요청 이후(=이번 task)의 도구 호출 수. 이전 task에서 넘어온 호출은 세지 않는다."""
    count = 0
    for message in reversed(messages):
        if isinstance(message, ModelRequest) and any(isinstance(p, UserPromptPart) for p in message.parts):
            break
        if isinstance(message, ModelResponse):
            count += sum(isinstance(p, ToolCallPart) for p in message.parts)
    return count


def choose_model_name(tool_calls: int, *, fast: str, smart: str) -> str:
    return smart if tool_calls > SWITCH_AFTER_TOOL_CALLS else fast


def build_model_selector(settings: Settings) -> Callable[[ModelSelectionContext[AgentDeps]], str]:
    def select(ctx: ModelSelectionContext[AgentDeps]) -> str:
        tool_calls = count_tool_calls_in_current_run(ctx.messages)
        return choose_model_name(tool_calls, fast=settings.agent_model_fast, smart=settings.agent_model_smart)

    return select


def build_model_resolver(settings: Settings) -> Callable[[ModelResolutionContext[Any], str], Model | None]:
    """'anthropic:...' 문자열을 설정의 API 키로 만든 AnthropicModel로 바꾼다. 다른 문자열은 기본 처리에 맡긴다.

    메인 에이전트(deps=AgentDeps)와 감시 에이전트(deps 없음)가 함께 쓰므로 deps 타입을 가리지 않는다.
    """

    def resolve(_ctx: ModelResolutionContext[Any], model_id: str) -> Model | None:
        if not model_id.startswith(ANTHROPIC_PREFIX):
            return None
        api_key = settings.anthropic_api_key.get_secret_value()
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY가 비어 있어 모델을 부를 수 없습니다.")
        provider = AnthropicProvider(api_key=api_key)
        return AnthropicModel(model_id.removeprefix(ANTHROPIC_PREFIX), provider=provider)

    return resolve
