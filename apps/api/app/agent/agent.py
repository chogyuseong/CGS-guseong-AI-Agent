"""메인 Agent 정의.

Agent 하나가 하는 일 (한 번의 agent.run 안에서 반복):

    사용자 요청 + 이전 기록
      ▼
    [SelectModel] 이번 요청에 쓸 모델을 고른다 (도구 호출 4번 이하 FAST, 넘으면 SMART)
      ▼
    [ResolveModelId] 'anthropic:모델ID' 문자열 → 설정의 API 키로 만든 AnthropicModel
      ▼
    모델 요청 (instructions = 시스템 프롬프트 + 오늘 날짜 + 기억 최근 30줄, 도구 목록 = origin별 toolset)
      ▼
    응답이 도구 호출이면 → [StepTracking]이 steps를 기록하며 도구 실행 → 결과를 모델에게 돌려주고 다시 위로
      단, EXTERNAL 도구(requires_approval)는 실행하지 않는다 → run 종료, result.output이 DeferredToolRequests
      (승인 대기. runner가 승인 요청을 저장하고, 사용자가 모두 결정하면 resume_task가 같은 기록으로 run을 잇는다)
    응답이 글이면     → run 종료, result.output이 최종 답변(str)

Agent는 앱이 시작할 때 한 번 만든다(build_agent). DBOS.launch() 전에 만들어져야 한다(main.py).

내구성 실행(3단계): DBOSDurability capability를 붙였다. agent.run()이 DBOS 워크플로(workflows.py) 안에서 불리면
모델 요청 하나하나가 DBOS 단계가 되어, 서버가 죽었다 살아나도 이미 받은 모델 응답은 다시 요청하지 않는다.
워크플로 밖에서 부르면 이 capability는 아무 일도 하지 않는다(보통 run).
- DBOS 단계 이름에 Agent 이름이 들어가므로 고유한 name('myagent')이 필요하다.
- 모델은 문자열로 고르므로(SelectModel) 등록할 필요가 없다. 문자열은 단계 안에서 ResolveModelId로 다시 만들어진다.
  모델 '객체'를 실행 중에 바꿔 끼우려면(테스트의 가짜 모델) models=에 등록해야 한다(extra_models).
- 도구 실행은 DBOS 단계가 아니다. 복구 때 같은 호출이 두 번 실행되지 않게 하는 일은 StepTracking이 한다(tracking.py).
- 도구 호출은 순서대로(sequential) 실행한다. 복구 때 단계 번호가 달라지지 않게 하고, step 순서도 정해진다.
"""

from collections.abc import Mapping

from pydantic_ai import Agent
from pydantic_ai.capabilities import ResolveModelId, SelectModel
from pydantic_ai.durable_exec.dbos import DBOSDurability
from pydantic_ai.models import Model
from pydantic_ai.tools import DeferredToolRequests

from app.agent.deps import AgentDeps
from app.agent.model_choice import build_model_resolver, build_model_selector
from app.agent.prompts import (
    SYSTEM_PROMPT,
    automation_instruction,
    current_date_instruction,
    memory_instruction,
)
from app.agent.tracking import StepTracking
from app.config import Settings

# 이 프로젝트의 Agent 타입: deps는 AgentDeps, 출력은 최종 답변(str) 또는 승인 대기(DeferredToolRequests).
type MyAgent = Agent[AgentDeps, str | DeferredToolRequests]

AGENT_NAME = "myagent"


def build_agent(settings: Settings, *, extra_models: Mapping[str, Model] | None = None) -> MyAgent:
    """extra_models: 워크플로 안에서 agent.override(model=...)로 끼울 모델 객체(테스트의 가짜 모델만 쓴다)."""
    return Agent(
        # 기본 모델. 실제로는 SelectModel이 요청마다 FAST/SMART를 고른다.
        settings.agent_model_fast,
        name=AGENT_NAME,
        deps_type=AgentDeps,
        output_type=[str, DeferredToolRequests],
        # 기억(memory_instruction)은 마지막에 둔다. 요청마다 DB에서 최근 30줄을 새로 읽는다(prompts.py).
        # automation_instruction(6단계)은 예약·감시 task에만 "자동 실행, 조회만, 행동은 [제안] 줄로"를 붙인다.
        instructions=[SYSTEM_PROMPT, current_date_instruction, automation_instruction, memory_instruction],
        capabilities=[
            ResolveModelId(build_model_resolver(settings)),
            SelectModel(build_model_selector(settings)),
            StepTracking(),
            DBOSDurability(models=extra_models, parallel_execution_mode="sequential"),
        ],
    )
