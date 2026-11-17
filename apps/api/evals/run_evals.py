"""eval 실행 명령(apps/api에서).

    uv run python -m evals.run_evals                 예상 비용만 보여 준다(모델을 부르지 않는다, 기본)
    uv run python -m evals.run_evals --run --email 내이메일 --confirm-cost [--repeat 3]
                                                     실제 모델로 30개를 돌린다(비용이 든다)

실제 실행의 안전 장치
1. --run과 --confirm-cost가 둘 다 있어야 모델을 부른다. 먼저 인자 없이 실행해 예상 비용을 보고 정한다.
2. dataset.yaml 맨 위의 "초안 — 사용자 확인 전" 표시를 지우기 전에는 거부한다(--allow-draft로만 넘김).
3. 사용자의 오늘 비용 + 예상 비용(상한)의 두 배가 DAILY_COST_LIMIT_USD를 넘으면 거부한다
   (eval 비용도 하루 한도에 들어간다).
4. 버리는 DB(myagent_eval)를 새로 만들어 쓰고 끝나면 지운다. 외부 API는 가짜, 승인은 하지 않는다(evals/harness.py).
5. 끝나면 개발 DB의 usage_logs에 purpose=eval로 사례·모델별 사용량을 남기고, 결과 JSON(evals/results/, 실행 기록 포함)과
   안전 경계·모델 판단을 나눈 요약, docs/EVALS.md 표 한 줄을 출력한다.
"""

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.agent.budget import cost_today
from app.config import Settings, get_settings
from app.db import create_engine_for, create_session_factory
from app.event_loop import run_async
from app.models import User
from evals.cases import DATASET_PATH, EvalDataset, load_dataset
from evals.database import create_eval_database, drop_eval_database, eval_database_url
from evals.estimate import MEASURED_HIGH, MEASURED_LOW, CostEstimate, baseline_from_usage_logs, estimate_cost
from evals.harness import build_environment, case_task, eval_settings, record_eval_usage
from evals.record import RunRecord, build_run_record, model_id
from evals.summary import EvalSummary, markdown_row, render_summary, summarize

DRAFT_MARK = "초안 — 사용자 확인 전"
RESULTS_DIR = Path(__file__).resolve().parent / "results"
MAX_REPEAT = 5


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m evals.run_evals", description="마이에이전트 eval")
    parser.add_argument("--run", action="store_true", help="실제 모델로 실행한다(비용이 든다)")
    parser.add_argument("--confirm-cost", action="store_true", help="예상 비용을 확인했다")
    parser.add_argument("--email", help="eval 비용을 기록할 계정(usage_logs, purpose=eval)")
    parser.add_argument("--cases", help="일부만: 사례 번호를 쉼표로(예: 01,17,29)")
    parser.add_argument("--repeat", type=int, default=1, help=f"사례마다 실행 횟수(1~{MAX_REPEAT}, 기본 1)")
    parser.add_argument("--allow-draft", action="store_true", help="초안 표시가 있어도 실행한다")
    args = parser.parse_args(argv)
    if not 1 <= args.repeat <= MAX_REPEAT:
        parser.error(f"--repeat는 1~{MAX_REPEAT}입니다.")
    return args


def selected(dataset: EvalDataset, cases: str | None) -> EvalDataset:
    if cases:
        prefixes = {f"{p.strip()}_" for p in cases.split(",") if p.strip()}
        dataset.cases = [c for c in dataset.cases if any((c.name or "").startswith(p) for p in prefixes)]
    return dataset


async def estimates(settings: Settings, dataset: EvalDataset, repeat: int) -> list[CostEstimate]:
    engine = create_engine_for(settings.database_url)
    try:
        async with create_session_factory(engine)() as session:
            measured = await baseline_from_usage_logs(session)
    finally:
        await engine.dispose()
    fast, guard = model_id(settings.agent_model_fast), model_id(settings.agent_model_guard)
    baselines = [b for b in (measured, MEASURED_LOW, MEASURED_HIGH) if b is not None]
    return [estimate_cost(dataset, b, model=fast, guard_model=guard, repeat=repeat) for b in baselines]


def print_estimates(found: list[CostEstimate]) -> None:
    for e in found:
        print(
            f"- {e.baseline.source}: 실행 {e.runs}건 × 입력 {e.baseline.input_tokens_per_case:,} + "
            f"출력 {e.baseline.output_tokens_per_case:,} 토큰, 감시 {e.guard_calls}회 → "
            f"약 ${e.total_usd:.4f} (메인 ${e.agent_usd:.4f} + 감시 ${e.guard_usd:.4f}, 모델 {e.model})"
        )


def print_preview(record: RunRecord, found: list[CostEstimate]) -> None:
    """모델을 부르지 않고 보여 주는 것: 실행 기록에 남을 값과 예상 비용."""
    commit = record.code_commit[:7] + ("*" if record.code_dirty else "")
    print(f"데이터셋 {record.dataset_name} {record.dataset_version}, 프롬프트 {record.prompt_version}, 코드 {commit}")
    print(f"고정 시각 {record.eval_clock}, 모델 {' / '.join(record.models.values())}")
    print(f"예상 비용(표본 {record.cases}개 × {record.repeat}회):")
    print_estimates(found)


async def owner_id(settings: Settings, email: str, worst_usd: float) -> int:
    """비용을 기록할 계정. 오늘 비용 + 예상 상한이 하루 한도를 넘으면 거부한다."""
    engine = create_engine_for(settings.database_url)
    try:
        async with create_session_factory(engine)() as session:
            user = await session.scalar(select(User).where(User.email == email))
            if user is None:
                raise SystemExit("그 이메일의 계정이 없습니다.")
            spent = float(await cost_today(session, user.id))
    finally:
        await engine.dispose()
    if spent + worst_usd > float(settings.daily_cost_limit_usd):
        raise SystemExit(
            f"오늘 비용 ${spent:.4f} + 예상 ${worst_usd:.4f}가 하루 한도를 넘습니다. 내일 다시 실행하세요."
        )
    return user.id


async def evaluate(settings: Settings, dataset: EvalDataset, record: RunRecord) -> Any:
    url = eval_database_url(settings.database_url)
    create_eval_database(url)
    engine = create_engine_for(url)
    try:
        env = build_environment(eval_settings(settings, url), create_session_factory(engine))
        return await dataset.evaluate(
            case_task(env),
            name="myagent-eval",
            max_concurrency=1,  # 사례마다 app.clock을 고정하므로 하나씩 돈다
            progress=True,
            repeat=record.repeat,
            metadata=asdict(record),
        )
    finally:
        await engine.dispose()
        drop_eval_database(url)


async def record_usage(settings: Settings, user_id: int, report: Any) -> int:
    engine = create_engine_for(settings.database_url)
    try:
        async with create_session_factory(engine)() as session:
            return await record_eval_usage(session, user_id, [case.output for case in report.cases])
    finally:
        await engine.dispose()


def case_row(case: Any) -> dict[str, Any]:
    return {
        "name": case.name,
        "spec_no": case.metadata.spec_no,
        "category": case.metadata.category,
        "assertions": {k: v.value for k, v in case.assertions.items()},
        "reasons": {k: v.reason for k, v in case.assertions.items() if v.reason},
        "seconds": round(case.task_duration, 2),
        "output": case.output.model_dump(),
    }


def save_results(report: Any, record: RunRecord, summary: EvalSummary) -> Path:
    """결과 JSON: 실행 기록 + 요약 + 사례별 결과. 파일 이름은 실행 시작 시각(커밋하지 않음)."""
    RESULTS_DIR.mkdir(exist_ok=True)
    failures = [{"name": f.name, "error": f.error_message} for f in report.failures]
    body = {
        "run": asdict(record),
        "summary": asdict(summary),
        "cases": [case_row(case) for case in report.cases],
        "failures": failures,
    }
    stamp = record.started_at[:16].replace("-", "").replace("T", "-").replace(":", "")
    path = RESULTS_DIR / f"{stamp}.json"
    path.write_text(json.dumps(body, ensure_ascii=False, indent=2, default=str), "utf-8")
    return path


async def run(args: argparse.Namespace, settings: Settings, dataset: EvalDataset) -> int:
    record = build_run_record(settings, dataset, repeat=args.repeat)
    found = await estimates(settings, dataset, args.repeat)
    print_preview(record, found)
    if not args.run:
        print("\n실제로 돌리려면: --run --email 내이메일 --confirm-cost (모델 비용이 듭니다)")
        return 0
    if not (args.confirm_cost and args.email):
        raise SystemExit("--run에는 --email과 --confirm-cost가 함께 필요합니다.")
    if not settings.anthropic_api_key.get_secret_value():
        raise SystemExit(".env에 ANTHROPIC_API_KEY가 없습니다.")
    user_id = await owner_id(settings, args.email, max(e.total_usd for e in found) * 2)
    report = await evaluate(settings, dataset, record)
    report.print(include_input=False, include_output=False)
    written = await record_usage(settings, user_id, report)
    summary = summarize(report, record)
    print(f"\n{render_summary(summary, record)}")
    print(f"\n결과: {save_results(report, record, summary)}  /  usage_logs(eval) {written}행")
    print(markdown_row(summary, record))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    dataset = selected(load_dataset(), args.cases)
    if args.run and DRAFT_MARK in DATASET_PATH.read_text(encoding="utf-8") and not args.allow_draft:
        raise SystemExit("dataset.yaml이 아직 초안입니다. 사례를 확인하고 맨 위 초안 표시를 지운 뒤 실행하세요.")
    return run_async(run(args, get_settings(), dataset))


if __name__ == "__main__":
    sys.exit(main())
