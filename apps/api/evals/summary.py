"""eval 결과를 docs/EVALS.md 표의 한 줄로 줄인다(도구 정답률, 승인 누락, 금지 거절, 주입 통과, 사례당 비용)."""

import hashlib
from collections import Counter
from dataclasses import dataclass
from typing import Any

from app.agent.prompts import SYSTEM_PROMPT
from evals.cases import EvalMetadata, EvalOutput

type Report = Any  # pydantic_evals EvaluationReport[EvalInputs, EvalOutput, EvalMetadata]


@dataclass(frozen=True)
class EvalSummary:
    cases: int
    task_errors: int
    tool_accuracy: float  # ToolSelection 통과 비율
    approval_misses: int  # ApprovalRequested 실패 수(반드시 0)
    refusal_rate: float  # 금지 요청 거절 비율
    injection_passed: int  # 주입 사례 중 모든 평가를 통과한 수
    injection_total: int
    external_writes: int  # 외부 쓰기 요청 수(반드시 0)
    cost_per_case_usd: float
    total_cost_usd: float
    failures_by_evaluator: dict[str, int]


def prompt_version() -> str:
    """시스템 프롬프트 내용의 해시 앞 8자리(프롬프트를 바꾸면 달라진다)."""
    return hashlib.sha256(SYSTEM_PROMPT.encode("utf-8")).hexdigest()[:8]


def passed(case: Any, name: str) -> bool | None:
    result = case.assertions.get(name)
    return None if result is None else bool(result.value)


def ratio(values: list[bool]) -> float:
    return round(sum(values) / len(values), 3) if values else 1.0


def summarize(report: Report) -> EvalSummary:
    cases = list(report.cases)
    outputs: list[EvalOutput] = [case.output for case in cases]
    meta: list[EvalMetadata] = [case.metadata for case in cases]
    tool = [passed(c, "ToolSelection") for c in cases]
    refusal = [passed(c, "ForbiddenRequestRefused") for c in cases]
    injection = [all(r.value for r in c.assertions.values()) for c, m in zip(cases, meta, strict=True)
                 if m.category == "injection"]  # fmt: skip
    failures = Counter(name for c in cases for name, r in c.assertions.items() if r.value is False)
    total = round(sum(o.cost_usd for o in outputs), 6)
    return EvalSummary(
        cases=len(cases),
        task_errors=len(report.failures),
        tool_accuracy=ratio([t for t in tool if t is not None]),
        approval_misses=failures.get("ApprovalRequested", 0),
        refusal_rate=ratio([r for r in refusal if r is not None]),
        injection_passed=sum(injection),
        injection_total=len(injection),
        external_writes=sum(len(o.external_writes) for o in outputs),
        cost_per_case_usd=round(total / len(cases), 6) if cases else 0.0,
        total_cost_usd=total,
        failures_by_evaluator=dict(failures),
    )


def markdown_row(summary: EvalSummary, *, day: str, models: str) -> str:
    """docs/EVALS.md 결과 표의 한 줄."""
    return (
        f"| {day} | {models} | {prompt_version()} | {summary.tool_accuracy:.0%} | {summary.approval_misses} | "
        f"{summary.refusal_rate:.0%} | {summary.injection_passed}/{summary.injection_total} | "
        f"${summary.cost_per_case_usd:.4f} | 사례 {summary.cases}개, 실행 오류 {summary.task_errors}, "
        f"외부 쓰기 {summary.external_writes} |"
    )
