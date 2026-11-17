"""모델 선택(1단계 보강, BUILD_SPEC 6.1).

- 일반 요청은 FAST 하나로 한다. 도구 호출 횟수만으로 SMART로 자동 전환하지 않는다
  (예전 명세의 "도구 호출 4번을 넘으면 SMART" 규칙을 뺐다). SMART는 평가 근거가 생긴 뒤 명시적 정책으로 도입한다.
- 모델 호출마다 MODEL_CALL_TIMEOUT_SECONDS를 건다.
"""

from typing import Any

from pydantic_ai.capabilities import AbstractCapability, SelectModel

from app.agent.agent import build_agent
from app.agent.guard import build_guard_agent
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
        assert agent.model_settings is not None
        assert agent.model_settings.get("timeout") == 42.0


def test_default_model_call_timeout(test_settings: Settings) -> None:
    assert test_settings.model_call_timeout_seconds == 60.0
