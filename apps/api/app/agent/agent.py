"""메인 Agent 정의.

Agent 하나가 하는 일 (한 번의 agent.run 안에서 반복):

    사용자 요청 + 이전 기록
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
- 모델은 문자열(AGENT_MODEL_FAST)이라 등록할 필요가 없다. 문자열은 단계 안에서 ResolveModelId로 다시 만들어진다.
  모델 '객체'를 실행 중에 바꿔 끼우려면(테스트의 가짜 모델) models=에 등록해야 한다(extra_models).
- 도구 실행은 DBOS 단계가 아니다. 복구 때 같은 호출이 두 번 실행되지 않게 하는 일은 StepTracking이 한다(tracking.py).
- 도구 호출은 순서대로(sequential) 실행한다. 복구 때 단계 번호가 달라지지 않게 하고, step 순서도 정해진다.

진입점: app/main.py lifespan이 build_agent로 만들어 app.state.agent에 둔다. eval은 evals/harness.py가 같은 함수로 만든다
(실제 앱과 같은 Agent). 실행은 runner.execute_agent가 agent.run(toolsets=toolsets.run_toolsets(origin), ...)으로 한다.
근거: BUILD_SPEC 6.1(FAST 하나), DECISIONS 2026-11-16(instructions로 넘김, toolset sequential),
2026-11-17(DBOSDurability를 Agent 생성 때 넣음, deps 타입을 가리지 않는 build_model_resolver).

고칠 때 주의:
- AGENT_NAME은 DBOS 단계 이름에 들어간다. 바꾸면 끝나지 않은 워크플로가 복구될 때 기록된 단계와 이름이 달라지므로
  workflows.WORKFLOW_VERSION을 함께 올리고 docs/OPERATIONS.md의 배포 절차를 따른다.
- output_type(str | DeferredToolRequests)을 바꾸면 runner의 output_type_for·결과 해석, 테스트의 가짜 모델 대본,
  eval이 함께 바뀐다(DECISIONS 2026-11-17 "AgentReply 구조화 출력은 미룬다").
  예약 task는 runner가 AutomationReply를 더한다.
- instructions(시스템 프롬프트·순서)를 바꾸면 모델 판단이 달라지므로 eval을 다시 돌려 본다(비용 확인 후, docs/EVALS.md).
- capability를 더하거나 빼면 BudgetCapability(호출 전 예약·후 정산)와 StepTracking(step 기록·취소 확인·복구 재생)이
  빠지지 않았는지 본다. 둘 중 하나라도 빠지면 예산 상한이나 도구 기록·복구 규칙이 깨진다.
"""

from collections.abc import Mapping

from pydantic_ai import Agent
from pydantic_ai.capabilities import ResolveModelId
from pydantic_ai.durable_exec.dbos import DBOSDurability
from pydantic_ai.models import Model
from pydantic_ai.tools import DeferredToolRequests

from app.agent.budget import BudgetCapability
from app.agent.deps import AgentDeps
from app.agent.model_choice import build_model_resolver, model_settings_for
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

# DBOS 단계 이름의 앞부분. 감시('myagent-guard')·감시 판정('myagent-watch') Agent와 겹치지 않아야 한다.
AGENT_NAME = "myagent"


def build_agent(settings: Settings, *, extra_models: Mapping[str, Model] | None = None) -> MyAgent:
    """메인 Agent를 만든다. 앱 시작 때 한 번, DBOS.launch() 전에 부른다(DBOSDurability가 launch 전에 등록돼야 한다).

    extra_models: 워크플로 안에서 agent.override(model=...)로 끼울 모델 객체(테스트의 가짜 모델만 쓴다).
    운영에서는 None이다. 등록하지 않은 모델 객체는 워크플로 안에서 끼울 수 없다(AGENTS.md 도구 규약 7).
    """
    return Agent(
        # 일반 요청은 FAST 하나로 한다. 도구 호출 횟수로 SMART로 바꾸지 않는다(BUILD_SPEC 6.1).
        settings.agent_model_fast,
        name=AGENT_NAME,
        deps_type=AgentDeps,
        output_type=[str, DeferredToolRequests],
        model_settings=model_settings_for(settings),
        # 기억(memory_instruction)은 마지막에 둔다. 요청마다 DB에서 최근 30줄을 새로 읽는다(prompts.py).
        # automation_instruction(6단계)은 예약·감시 task에만 "자동 실행, 조회만, 행동은 suggestions로"를 붙인다.
        instructions=[SYSTEM_PROMPT, current_date_instruction, automation_instruction, memory_instruction],
        capabilities=[
            ResolveModelId(build_model_resolver(settings)),
            BudgetCapability(),
            StepTracking(),
            DBOSDurability(models=extra_models, parallel_execution_mode="sequential"),
        ],
    )
