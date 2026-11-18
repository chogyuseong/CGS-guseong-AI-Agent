"""eval 한 번의 예상 비용. 실제 eval을 돌리기 전에 사용자에게 보여 준다(명세: 예상 비용을 알리고 확인받기).

예상 = 사례 수 × (사례당 메인 에이전트 토큰 × 가격) + 승인 사례 수 × (감시 에이전트 토큰 × 가격)
- 가격은 app/config.py의 MODEL_PRICES(공식 가격표)를 budget.calculate_cost로 쓴다.
- 사례당 토큰은 개발 DB usage_logs의 실제 평균(채팅 task별 agent 행 합계의 평균, guard 행 평균)을 쓰고,
  기록이 적으면 지금까지의 실측 범위(요청 하나에 약 11,000~18,000 토큰)를 쓴다.

흐름 속 위치: run_evals.run이 모델을 부르기 전에 estimates → estimate_cost로 값을 만들어 보여 준다(인자 없는 실행은
여기서 끝난다). --run이면 가장 큰 예상의 두 배를 run_evals.owner_id의 하루 한도 검사에 쓴다.

근거: BUILD_SPEC 8단계(예상 비용을 알리고 확인받은 뒤 실행), DECISIONS 2026-11-17(eval 비용도 usage_logs와
하루 한도에 들어감).

고칠 때 주의
- 가격(app/config.py MODEL_PRICES)이나 MEASURED_LOW·MEASURED_HIGH를 바꾸면 docs/EVALS.md '예상 비용' 표를
  새 출력으로 다시 쓴다. tests/test_evals.py의 test_estimate_uses_model_prices_and_repeat도 본다.
- 감시 호출 수는 cases.EvalMetadata.expects_approval(승인 도구가 있는 정답 경로)에서 온다. dataset.yaml의 paths를
  바꾸면 감시 호출 수가 달라진다.
- 이 파일은 개발 DB를 읽기만 한다(쓰기 없음).

변경 이력
- 2026-11-17 처음 작성(8단계).
- 2026-11-18 주석 보강(동작 변경 없음).
"""

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.budget import calculate_cost
from app.models import UsageLog
from app.models.enums import UsagePurpose
from evals.cases import EvalDataset

# 채팅 task가 이보다 적으면 평균이 몇 건의 특이한 요청에 끌려가므로 실측 범위를 쓴다(docs/EVALS.md '예상 비용' 근거).
MIN_TASKS_FOR_AVERAGE = 5


@dataclass(frozen=True)
class UsageBaseline:
    """예상 비용의 가정 하나: 사례당 메인 에이전트 토큰, 감시 에이전트 한 번의 토큰, 출처(화면에 그대로 보임)."""

    input_tokens_per_case: int
    output_tokens_per_case: int
    guard_input_tokens: int
    guard_output_tokens: int
    source: str


# 고칠 때: 값을 바꾸면 docs/EVALS.md '예상 비용' 표(하한·상한 두 줄)를 새 출력으로 다시 쓴다.
# 메인이 실제 모델로 확인한 범위(2026-11-17): 요청 하나(task)에 약 11,000~18,000 토큰, 건당 약 $0.0015~0.002.
# 입력이 대부분이다(시스템 프롬프트 + 도구 설명 + 이전 기록이 모델 요청마다 다시 실린다).
MEASURED_LOW = UsageBaseline(10_500, 500, 700, 60, "실측 범위 하한(11,000 토큰/요청)")
MEASURED_HIGH = UsageBaseline(17_000, 1_000, 900, 80, "실측 범위 상한(18,000 토큰/요청)")


@dataclass(frozen=True)
class CostEstimate:
    """가정 하나로 계산한 예상 비용(달러). total_usd는 run_evals가 하루 한도 검사에 쓴다."""

    cases: int  # 표본(사례) 수
    runs: int  # 사례 × 실행 횟수
    guard_calls: int
    agent_usd: float
    guard_usd: float
    total_usd: float
    baseline: UsageBaseline
    model: str


def estimate_cost(
    dataset: EvalDataset, baseline: UsageBaseline, *, model: str, guard_model: str | None = None, repeat: int = 1
) -> CostEstimate:
    """사례 수 × repeat × 사례당 비용 + 감시 호출 수 × 감시 한 번 비용. 모델을 부르지 않는 순수 계산이다.

    guard_model을 주지 않으면 메인 모델 가격을 쓴다. 가격표(MODEL_PRICES)에 없는 모델이면 calculate_cost가 0을
    돌려주므로(경고 로그만) 예상이 $0으로 나온다. 그때는 실행하지 말고 가격표부터 채운다.
    """
    cases = len(dataset.cases)
    # '고쳐서 다시' 사례(inputs.revision_note)는 고친 뒤 한 번 더 실행하므로 실행·감시가 두 배다(2026-11-18).
    rounds = [2 if c.inputs.revision_note else 1 for c in dataset.cases]
    runs = sum(rounds) * repeat
    # 승인이 필요한 정답 경로가 있는 사례마다 감시 에이전트 한 번(되묻기를 고르면 부르지 않으므로 상한 쪽 가정).
    guard_calls = repeat * sum(
        n for c, n in zip(dataset.cases, rounds, strict=True) if c.metadata is not None and c.metadata.expects_approval
    )
    per_case = calculate_cost(model, baseline.input_tokens_per_case, baseline.output_tokens_per_case)
    per_guard = calculate_cost(guard_model or model, baseline.guard_input_tokens, baseline.guard_output_tokens)
    agent_usd, guard_usd = float(per_case * runs), float(per_guard * guard_calls)
    return CostEstimate(
        cases=cases,
        runs=runs,
        guard_calls=guard_calls,
        agent_usd=agent_usd,
        guard_usd=guard_usd,
        total_usd=round(agent_usd + guard_usd, 6),
        baseline=baseline,
        model=model,
    )


async def baseline_from_usage_logs(session: AsyncSession) -> UsageBaseline | None:
    """개발 DB의 실제 평균. 채팅 task가 MIN_TASKS_FOR_AVERAGE개보다 적으면 None(실측 범위를 쓴다). 읽기만 한다.

    - 메인: purpose=agent이고 task가 있는 행을 task별로 합친 뒤 평균(한 요청 = 사례 하나로 본다).
    - 감시: purpose=guard 행 하나의 평균. 기록이 없으면 MEASURED_HIGH 값을 쓴다(적게 잡지 않으려고).
    - 사용자 조건 없이 개발 DB 전체의 평균이다(--email 계정만의 평균이 아니다).
    """
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
