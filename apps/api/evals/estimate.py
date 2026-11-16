"""eval 한 번의 예상 비용. 실제 eval을 돌리기 전에 사용자에게 보여 준다(명세: 예상 비용을 알리고 확인받기).

예상 = 사례 수 × (사례당 메인 에이전트 토큰 × 가격) + 승인 사례 수 × (감시 에이전트 토큰 × 가격)
- 가격은 app/config.py의 MODEL_PRICES(공식 가격표)를 budget.calculate_cost로 쓴다.
- 사례당 토큰은 개발 DB usage_logs의 실제 평균(채팅 task별 agent 행 합계의 평균, guard 행 평균)을 쓰고,
  기록이 적으면 지금까지의 실측 범위(요청 하나에 약 11,000~18,000 토큰)를 쓴다.
"""

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.budget import calculate_cost
from app.models import UsageLog
from app.models.enums import UsagePurpose
from evals.cases import EvalDataset

MIN_TASKS_FOR_AVERAGE = 5


@dataclass(frozen=True)
class UsageBaseline:
    input_tokens_per_case: int
    output_tokens_per_case: int
    guard_input_tokens: int
    guard_output_tokens: int
    source: str


# 메인이 실제 모델로 확인한 범위(2026-11-17): 요청 하나(task)에 약 11,000~18,000 토큰, 건당 약 $0.0015~0.002.
# 입력이 대부분이다(시스템 프롬프트 + 도구 설명 + 이전 기록이 모델 요청마다 다시 실린다).
MEASURED_LOW = UsageBaseline(10_500, 500, 700, 60, "실측 범위 하한(11,000 토큰/요청)")
MEASURED_HIGH = UsageBaseline(17_000, 1_000, 900, 80, "실측 범위 상한(18,000 토큰/요청)")


@dataclass(frozen=True)
class CostEstimate:
    cases: int
    guard_calls: int
    agent_usd: float
    guard_usd: float
    total_usd: float
    baseline: UsageBaseline
    model: str


def estimate_cost(
    dataset: EvalDataset, baseline: UsageBaseline, *, model: str, guard_model: str | None = None
) -> CostEstimate:
    cases = len(dataset.cases)
    guard_calls = sum(1 for c in dataset.cases if c.metadata is not None and c.metadata.approval_required)
    per_case = calculate_cost(model, baseline.input_tokens_per_case, baseline.output_tokens_per_case)
    per_guard = calculate_cost(guard_model or model, baseline.guard_input_tokens, baseline.guard_output_tokens)
    agent_usd, guard_usd = float(per_case * cases), float(per_guard * guard_calls)
    return CostEstimate(
        cases=cases,
        guard_calls=guard_calls,
        agent_usd=agent_usd,
        guard_usd=guard_usd,
        total_usd=round(agent_usd + guard_usd, 6),
        baseline=baseline,
        model=model,
    )


async def baseline_from_usage_logs(session: AsyncSession) -> UsageBaseline | None:
    """개발 DB의 실제 평균. 채팅 task가 MIN_TASKS_FOR_AVERAGE개보다 적으면 None(실측 범위를 쓴다). 읽기만 한다."""
    per_task = (
        select(
            func.sum(UsageLog.input_tokens).label("input_tokens"),
            func.sum(UsageLog.output_tokens).label("output_tokens"),
        )
        .where(UsageLog.purpose == UsagePurpose.AGENT, UsageLog.task_id.is_not(None))
        .group_by(UsageLog.task_id)
        .subquery()
    )
    agent = (
        await session.execute(
            select(func.count(), func.avg(per_task.c.input_tokens), func.avg(per_task.c.output_tokens))
        )
    ).one()
    guard = (
        await session.execute(
            select(func.avg(UsageLog.input_tokens), func.avg(UsageLog.output_tokens)).where(
                UsageLog.purpose == UsagePurpose.GUARD
            )
        )
    ).one()
    if int(agent[0] or 0) < MIN_TASKS_FOR_AVERAGE:
        return None
    return UsageBaseline(
        input_tokens_per_case=int(agent[1]),
        output_tokens_per_case=int(agent[2]),
        guard_input_tokens=int(guard[0] or MEASURED_HIGH.guard_input_tokens),
        guard_output_tokens=int(guard[1] or MEASURED_HIGH.guard_output_tokens),
        source=f"개발 DB usage_logs 평균(채팅 task {int(agent[0])}개)",
    )
