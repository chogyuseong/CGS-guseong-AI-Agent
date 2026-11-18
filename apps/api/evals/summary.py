"""eval 결과 요약(8단계 보강). 안전 경계는 건수로, 모델 판단은 통과율로 따로 보고한다(BUILD_SPEC 16절
"안전 지표는 평균 점수에 묻지 말고 별도로 보고"). 표본 수와 실행 횟수를 함께 적고, 표본에서 0건인 것을
시스템 전체의 보장으로 쓰지 않는다(BUILD_SPEC 8단계).

흐름 속 위치: run_evals.run이 Dataset.evaluate의 보고서(EvaluationReport)와 record.RunRecord를 받아 summarize →
render_summary(터미널), markdown_row(docs/EVALS.md 결과 표 한 줄)를 부르고, 요약은 결과 JSON의 "summary"에도 들어간다.
읽기만 하고 DB·파일을 쓰지 않는다.

고칠 때 주의
- SAFETY_EVALUATORS·JUDGMENT_EVALUATORS의 키는 evaluators.py 클래스 이름(결과 assertion 이름)과 같아야 한다.
  어긋나면 그 지표가 '해당 없음'(0/0)으로 보여 위반이 있어도 숨는다. 평가기를 더하면 둘 중 한쪽에 넣는다.
  CaseCost는 점수(cost_usd·total_tokens)라 어느 쪽에도 넣지 않았다.
- markdown_row의 칸 순서는 docs/EVALS.md 결과 표 머리와 같아야 한다. 칸을 바꾸면 표 머리와 이전 줄을 함께 고친다.
- tests/test_evals.py(test_summary_reports_safety_separately 등)가 요약 모양을 확인한다.

변경 이력
- 2026-11-17 처음 작성(8단계). 같은 날 8단계 보강에서 안전 경계·모델 판단 분리, 실행 기록 줄, 가장 약한 유형을 더함.
- 2026-11-18 주석 보강(동작 변경 없음).
"""

from collections import defaultdict
from dataclasses import dataclass
from statistics import mean
from typing import Any

from evals.cases import EvalMetadata, EvalOutput
from evals.record import RunRecord

type Report = Any  # pydantic_evals EvaluationReport[EvalInputs, EvalOutput, EvalMetadata]

# 안전 경계: 건수로만 보고하고 평균·유형 통과율에 넣지 않는다(BUILD_SPEC 16절). 값은 화면에 보이는 한국어 이름.

SAFETY_EVALUATORS: dict[str, str] = {
    "ApprovalBypass": "승인 전 외부 실행",
    "ApprovalScope": "승인 범위 불일치",
    "ForbiddenActionExecuted": "금지 행동 실행",
    "FalseCompletionClaim": "잘못된 완료 주장",
    "PrivacyTransfer": "개인정보 전송 위반",
}
# 모델 판단: 통과율로 보고하고, 유형별 통과율(가장 약한 유형)도 이 평가기들로만 계산한다.
JUDGMENT_EVALUATORS: dict[str, str] = {
    "ToolSelection": "요청 해결·도구 선택",
    "ToolArguments": "인자·대상",
    "Clarification": "적절한 되묻기",
    "ForbiddenToolAvoided": "금지 도구 시도 없음",
    "RefusalStated": "금지 요청 거절 표현",
    "SourceGrounding": "출처 근거",
    "ResultClaims": "결과 주장 일치",
    "AnswerKeywords": "답변 핵심어",
}
# 보고에 늘 붙는 문장(BUILD_SPEC 8단계). 0건을 '안전하다'로 읽지 않게 한다.
NOT_A_GUARANTEE = "표본에서 0건이어도 시스템 전체에 대한 수학적 보장이 아니다."


@dataclass(frozen=True)
class MetricCount:
    """평가기 하나의 집계. applicable이 0이면 rate는 None('해당 없음')이다."""

    name: str
    label: str
    applicable: int  # 이 평가가 해당한 결과 수(사례 × 실행 횟수)
    failed: int

    @property
    def passed(self) -> int:
        return self.applicable - self.failed

    @property
    def rate(self) -> float | None:
        return round(self.passed / self.applicable, 3) if self.applicable else None


@dataclass(frozen=True)
class EvalSummary:
    """한 번의 eval 실행 요약(결과 JSON의 "summary"). 비용은 실행 오류 없이 끝난 결과만 합친다."""

    cases: int  # 표본(사례) 수
    runs: int  # 사례마다 실행 횟수
    results: int  # 평가된 결과 수
    task_errors: int
    safety: list[MetricCount]
    judgment: list[MetricCount]
    weakest_categories: list[tuple[str, float]]  # 모델 판단 통과율이 낮은 유형부터
    cost_per_case_usd: float
    total_cost_usd: float
    mean_seconds: float


def count(cases: list[Any], name: str, label: str) -> MetricCount:
    """평가기가 {}(해당 없음)를 돌려준 사례는 assertions에 이름이 없어 분모에서 빠진다. 실패는 value가 False인 것."""
    results = [case.assertions[name] for case in cases if name in case.assertions]
    return MetricCount(name, label, len(results), sum(1 for r in results if r.value is False))


def category_rates(cases: list[Any]) -> list[tuple[str, float]]:
    """유형(schedule 등)별 모델 판단 통과율, 낮은 것부터. 안전 경계는 섞지 않는다(평균에 묻지 않기)."""
    totals: dict[str, list[bool]] = defaultdict(list)
    for case in cases:
        meta: EvalMetadata = case.metadata
        totals[meta.category] += [bool(r.value) for n, r in case.assertions.items() if n in JUDGMENT_EVALUATORS]
    rates = [(category, round(sum(v) / len(v), 3)) for category, v in totals.items() if v]
    return sorted(rates, key=lambda item: item[1])


def summarize(report: Report, record: RunRecord) -> EvalSummary:
    """보고서를 요약으로 바꾼다. 표본 수·회수는 record에서, 결과 수·오류 수는 보고서에서 온다.

    실행 오류(report.failures)는 출력이 없어 지표·비용 계산에서 빠지고 task_errors로만 센다.
    """
    cases = list(report.cases)
    outputs: list[EvalOutput] = [case.output for case in cases]
    total = round(sum(o.cost_usd for o in outputs), 6)
    return EvalSummary(
        cases=record.cases,
        runs=record.repeat,
        results=len(cases),
        task_errors=len(report.failures),
        safety=[count(cases, name, label) for name, label in SAFETY_EVALUATORS.items()],
        judgment=[count(cases, name, label) for name, label in JUDGMENT_EVALUATORS.items()],
        weakest_categories=category_rates(cases),
        cost_per_case_usd=round(total / len(cases), 6) if cases else 0.0,
        total_cost_usd=total,
        mean_seconds=round(mean(case.task_duration for case in cases), 2) if cases else 0.0,
    )


def percent(metric: MetricCount) -> str:
    """'통과/해당 (비율)' 글자. 결과 표와 터미널 보고가 같은 모양을 쓴다."""
    return "해당 없음" if metric.rate is None else f"{metric.passed}/{metric.applicable} ({metric.rate:.0%})"


def record_lines(record: RunRecord) -> list[str]:
    """보고 맨 위의 실행 기록 줄(어떤 모델·프롬프트·코드·데이터셋으로 돌렸는지)."""
    dirty = {True: " (커밋하지 않은 변경 있음)", False: "", None: " (상태 모름)"}[record.code_dirty]
    models = record.models
    return [
        f"실행 {record.started_at}, 고정 시각 {record.eval_clock}",
        f"모델 FAST {models['fast']} / SMART {models['smart']} / GUARD {models['guard']}",
        f"프롬프트 {record.prompt_version} (sha256 {record.prompt_sha256}), 코드 {record.code_commit}{dirty}",
        f"데이터셋 {record.dataset_name} {record.dataset_version}",
    ]


def render_summary(summary: EvalSummary, record: RunRecord) -> str:
    """터미널에 보여 줄 보고. 안전 경계와 모델 판단을 나눈다."""
    lines = record_lines(record)
    lines.append(
        f"표본 {summary.cases}개 × {summary.runs}회 = 결과 {summary.results}개, 실행 오류 {summary.task_errors}"
    )
    lines.append("안전 경계(건수, 평균에 넣지 않음):")
    lines += [f"- {m.label}: {m.failed}건 / 해당 {m.applicable}" for m in summary.safety]
    lines.append(f"  {NOT_A_GUARANTEE}")
    lines.append("모델 판단(통과율):")
    lines += [f"- {m.label}: {percent(m)}" for m in summary.judgment]
    weakest = ", ".join(f"{category} {rate:.0%}" for category, rate in summary.weakest_categories[:3])
    lines.append(f"가장 약한 유형: {weakest or '없음'}")
    lines.append(
        f"비용 합계 ${summary.total_cost_usd:.4f}, 사례당 ${summary.cost_per_case_usd:.4f}, "
        f"평균 지연 {summary.mean_seconds:.1f}초"
    )
    return "\n".join(lines)


def markdown_row(summary: EvalSummary, record: RunRecord) -> str:
    """docs/EVALS.md 결과 표의 한 줄. 사람이 복사해 붙인다(이 함수는 문서를 고치지 않는다).

    코드 칸 뒤 *는 커밋하지 않은 변경이 있었다는 뜻이다. 칸 순서는 문서의 표 머리와 같아야 한다.
    """
    safety = " / ".join(str(m.failed) for m in summary.safety)
    judged = {m.name: percent(m) for m in summary.judgment}
    models = " / ".join(record.models.values())
    commit = record.code_commit[:7] + ("*" if record.code_dirty else "")
    return (
        f"| {record.started_at[:10]} | {models} | {record.prompt_version} | {commit} | {record.dataset_version} | "
        f"{summary.cases}×{summary.runs} | {safety} | {judged['ToolSelection']} | {judged['ToolArguments']} | "
        f"{judged['Clarification']} | {judged['SourceGrounding']} | ${summary.cost_per_case_usd:.4f} | "
        f"{summary.mean_seconds:.1f}초 |"
    )
