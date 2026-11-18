"""모델 문자열 해석과 모델 호출 설정.

1단계 보강(BUILD_SPEC 6.1): 예전의 "도구 호출이 4번을 넘으면 SMART로 전환"(SelectModel)을 뺐다.
일반 요청은 AGENT_MODEL_FAST 하나로 한다. SMART는 평가 근거가 생긴 뒤 명시적 정책으로 도입한다(docs/DECISIONS.md).

- ResolveModelId capability: 'anthropic:모델ID' 문자열을 실제 모델 객체로 만든다. API 키를 os.environ이 아니라
  설정(.env를 읽은 Settings)에서 넘기기 위해 필요하다. 모델을 "문자열"로 두므로 DBOSDurability와 충돌하지 않는다
  (DBOS 문서: 모델 이름 문자열은 등록 없이 단계 안에서 다시 만들어진다).
- 테스트에서 agent.override(model=FunctionModel)로 모델을 지정하면 해석을 건너뛴다(명시한 모델이 우선).

쓰는 곳: agent.build_agent(메인), guard.build_guard_agent(감시), app/automation/watch.py(감시 판정). 세 Agent가 같은
resolver와 호출 설정을 쓴다. 근거: DECISIONS 2026-11-16(모델 ID), 2026-11-17(deps 타입을 가리지 않는 resolver).

고칠 때 주의:
- 다른 제공자(anthropic: 이외)나 새 모델을 쓰려면 resolver에 분기를 더하고, 가격을 app/config.py의 MODEL_PRICES에
  넣는다(가격표에 없는 모델은 budget.py가 UnpricedModelError로 부르지 않는다).
  resolve가 None을 돌려주면 Pydantic AI 기본 처리(os.environ의 키)로 넘어가 .env 설정을 거치지 않게 된다.
- max_tokens(MAX_OUTPUT_TOKENS)는 budget.py의 예약액 계산과 같은 값이어야 한다
  (budget.py에서 정의하고 여기서는 가져다 쓴다).
- 테스트: tests/test_model_choice.py.
"""

from collections.abc import Callable
from typing import Any

from pydantic_ai.models import Model, ModelResolutionContext
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.settings import ModelSettings

from app.agent.budget import MAX_OUTPUT_TOKENS
from app.config import Settings

# Pydantic AI 모델 문자열의 제공자 접두사. .env의 AGENT_MODEL_* 값이 이 모양이다(DECISIONS 2026-11-16).
ANTHROPIC_PREFIX = "anthropic:"


def model_settings_for(settings: Settings) -> ModelSettings:
    """세 에이전트(메인, 감시, 감시 판정)가 함께 쓰는 호출 설정. 제한 시간과 출력 상한(예산 예상의 기준).

    제한 시간은 .env의 모델 호출 제한(settings.model_call_timeout_seconds)이다. 시간 초과는 budget.py에서
    결과 불명(예약액을 풀지 않음)으로 처리된다.
    """
    return ModelSettings(timeout=settings.model_call_timeout_seconds, max_tokens=MAX_OUTPUT_TOKENS)


def build_model_resolver(settings: Settings) -> Callable[[ModelResolutionContext[Any], str], Model | None]:
    """'anthropic:...' 문자열을 설정의 API 키로 만든 AnthropicModel로 바꾼다. 다른 문자열은 기본 처리에 맡긴다.

    메인 에이전트(deps=AgentDeps)와 감시 에이전트(deps 없음)가 함께 쓰므로 deps 타입을 가리지 않는다.
    키가 비어 있으면 RuntimeError(모델 요청 때 난다. 메시지에 키 값은 넣지 않는다).
    키는 .env를 읽은 Settings(SecretStr)에서만 꺼낸다(절대 원칙 15).
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
