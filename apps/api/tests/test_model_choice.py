"""모델 선택(1단계 보강, BUILD_SPEC 6.1).

- 일반 요청은 FAST 하나로 한다. 도구 호출 횟수만으로 SMART로 자동 전환하지 않는다
  (예전 명세의 "도구 호출 4번을 넘으면 SMART" 규칙을 뺐다). SMART는 평가 근거가 생긴 뒤 명시적 정책으로 도입한다.
- 모델 호출마다 MODEL_CALL_TIMEOUT_SECONDS를 건다.
"""

from typing import Any, cast

from pydantic import SecretStr
from pydantic_ai.capabilities import AbstractCapability, SelectModel
from pydantic_ai.models import ModelResolutionContext
from pydantic_ai.models.anthropic import AnthropicModel

from app.agent.agent import build_agent
from app.agent.guard import build_guard_agent
from app.agent.model_choice import build_model_resolver
from app.automation.watch import build_watch_agent
from app.config import Settings


def capability_types(agent: Any) -> set[type]:
    found: set[type] = set()

    def visit(capability: AbstractCapability[Any]) -> None:
        found.add(type(capability))

    agent.root_capability.apply(visit)
    return found


def test_main_agent_uses_fast_only_without_model_switch(test_settings: Settings) -> None:
    agent = build_agent(test_settings)

    assert SelectModel not in capability_types(agent)
    assert agent.model == test_settings.agent_model_fast


def test_model_call_timeout_is_applied_to_every_agent(test_settings: Settings) -> None:
    test_settings.model_call_timeout_seconds = 42.0

    for agent in (build_agent(test_settings), build_guard_agent(test_settings), build_watch_agent(test_settings)):
        # model_settings는 dict 또는 함수일 수 있다. 이 앱은 dict(ModelSettings)로 넘긴다.
        model_settings = agent.model_settings
        assert isinstance(model_settings, dict)
        assert model_settings.get("timeout") == 42.0


def test_default_model_call_timeout(test_settings: Settings) -> None:
    assert test_settings.model_call_timeout_seconds == 60.0


def test_resolver_reuses_one_anthropic_client(test_settings: Settings) -> None:
    """모델을 부를 때마다 HTTP 클라이언트를 새로 만들지 않는다(연결 재사용, 닫히지 않는 클라이언트 누적 방지).

    네트워크 호출은 없다. 모델 객체만 만들어 같은 SDK 클라이언트를 쓰는지 본다.
    """
    settings = test_settings.model_copy(update={"anthropic_api_key": SecretStr("test-key-not-real")})
    resolve = build_model_resolver(settings)
    context = cast(ModelResolutionContext[Any], None)

    first = resolve(context, settings.agent_model_fast)
    second = resolve(context, settings.agent_model_guard)

    assert isinstance(first, AnthropicModel)
    assert isinstance(second, AnthropicModel)
    assert first.client is second.client
