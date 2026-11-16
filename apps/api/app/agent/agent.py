"""메인 Agent 정의.

Agent 하나가 하는 일 (한 번의 agent.run 안에서 반복):

    사용자 요청 + 이전 기록
      ▼
    [SelectModel] 이번 요청에 쓸 모델을 고른다 (도구 호출 4번 이하 FAST, 넘으면 SMART)
      ▼
    [ResolveModelId] 'anthropic:모델ID' 문자열 → 설정의 API 키로 만든 AnthropicModel
      ▼
    모델 요청 (instructions = 시스템 프롬프트 + 오늘 날짜, 도구 목록 = origin별 toolset)
      ▼
    응답이 도구 호출이면 → [StepTracking]이 steps를 기록하며 도구 실행 → 결과를 모델에게 돌려주고 다시 위로
    응답이 글이면     → run 종료, result.output이 최종 답변

Agent는 앱이 시작할 때 한 번 만든다(build_agent). 3단계에서 DBOSDurability capability를 여기에 더한다.
DBOS는 Agent에 고유한 name을 요구하므로 미리 'myagent'로 정해 둔다.
2단계에서 output_type에 DeferredToolRequests(승인 대기)를 더한다.
"""

from pydantic_ai import Agent
from pydantic_ai.capabilities import ResolveModelId, SelectModel

from app.agent.deps import AgentDeps
from app.agent.model_choice import build_model_resolver, build_model_selector
from app.agent.prompts import SYSTEM_PROMPT, current_date_instruction
from app.agent.tracking import StepTracking
from app.config import Settings

# 이 프로젝트의 Agent 타입: deps는 AgentDeps, 최종 출력은 문자열.
type MyAgent = Agent[AgentDeps, str]

AGENT_NAME = "myagent"


def build_agent(settings: Settings) -> MyAgent:
    return Agent(
        # 기본 모델. 실제로는 SelectModel이 요청마다 FAST/SMART를 고른다.
        settings.agent_model_fast,
        name=AGENT_NAME,
        deps_type=AgentDeps,
        output_type=str,
        instructions=[SYSTEM_PROMPT, current_date_instruction],
        capabilities=[
            ResolveModelId(build_model_resolver(settings)),
            SelectModel(build_model_selector(settings)),
            StepTracking(),
        ],
    )
