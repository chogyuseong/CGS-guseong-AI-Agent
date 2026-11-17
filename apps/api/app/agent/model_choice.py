"""모델 문자열 해석과 모델 호출 설정.

1단계 보강(BUILD_SPEC 6.1): 예전의 "도구 호출이 4번을 넘으면 SMART로 전환"(SelectModel)을 뺐다.
일반 요청은 AGENT_MODEL_FAST 하나로 한다. SMART는 평가 근거가 생긴 뒤 명시적 정책으로 도입한다(docs/DECISIONS.md).

- ResolveModelId capability: 'anthropic:모델ID' 문자열을 실제 모델 객체로 만든다. API 키를 os.environ이 아니라
  설정(.env를 읽은 Settings)에서 넘기기 위해 필요하다. 모델을 "문자열"로 두므로 DBOSDurability와 충돌하지 않는다
  (DBOS 문서: 모델 이름 문자열은 등록 없이 단계 안에서 다시 만들어진다).
- 테스트에서 agent.override(model=FunctionModel)로 모델을 지정하면 해석을 건너뛴다(명시한 모델이 우선).
"""

from collections.abc import Callable
from typing import Any

from pydantic_ai.models import Model, ModelResolutionContext
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.settings import ModelSettings

from app.config import Settings

ANTHROPIC_PREFIX = "anthropic:"


def model_settings_for(settings: Settings) -> ModelSettings:
    """세 에이전트(메인, 감시, 감시 판정)가 함께 쓰는 호출 설정. 호출 한 번의 제한 시간을 건다."""
    return ModelSettings(timeout=settings.model_call_timeout_seconds)


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
