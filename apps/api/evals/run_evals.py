"""eval 실행 명령(apps/api에서).

    uv run python -m evals.run_evals                 예상 비용만 보여 준다(모델을 부르지 않는다, 기본)
    uv run python -m evals.run_evals --run --email 내이메일 --confirm-cost
                                                     실제 모델로 30개를 돌린다(비용이 든다)

실제 실행의 안전 장치
1. --run과 --confirm-cost가 둘 다 있어야 모델을 부른다. 먼저 인자 없이 실행해 예상 비용을 보고 정한다.
2. dataset.yaml 맨 위의 "초안 — 사용자 확인 전" 표시를 지우기 전에는 거부한다(--allow-draft로만 넘김).
3. 사용자의 오늘 비용 + 예상 비용(상한)이 DAILY_COST_LIMIT_USD를 넘으면 거부한다(eval 비용도 하루 한도에 들어간다).
4. 버리는 DB(myagent_eval)를 새로 만들어 쓰고 끝나면 지운다. 외부 API는 가짜, 승인은 하지 않는다(evals/harness.py).
5. 끝나면 개발 DB의 usage_logs에 purpose=eval로 사례·모델별 사용량을 남기고,
   결과 JSON(evals/results/)과 docs/EVALS.md 표 한 줄을 출력한다.
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.agent.budget import cost_today
from app.agent.prompts import SEOUL
from app.config import Settings, get_settings
from app.db import create_engine_for, create_session_factory
from app.event_loop import run_async
from app.models import User
from evals.cases import DATASET_PATH, EvalDataset, load_dataset
from evals.database import create_eval_database, drop_eval_database, eval_database_url
from evals.estimate import MEASURED_HIGH, MEASURED_LOW, CostEstimate, baseline_from_usage_logs, estimate_cost
from evals.harness import build_environment, case_task, eval_settings, record_eval_usage
from evals.summary import markdown_row, summarize

DRAFT_MARK = "초안 — 사용자 확인 전"
RESULTS_DIR = Path(__file__).resolve().parent / "results"


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m evals.run_evals", description="마이에이전트 eval")
    parser.add_argument("--run", action="store_true", help="실제 모델로 실행한다(비용이 든다)")
    parser.add_argument("--confirm-cost", action="store_true", help="예상 비용을 확인했다")
    parser.add_argument("--email", help="eval 비용을 기록할 계정(usage_logs, purpose=eval)")
    parser.add_argument("--cases", help="일부만: 사례 번호 앞부분을 쉼표로(예: 01,17,29)")
    parser.add_argument("--allow-draft", action="store_true", help="초안 표시가 있어도 실행한다")
    return parser.parse_args(argv)


def selected(dataset: EvalDataset, cases: str | None) -> EvalDataset:
    if cases:
        prefixes = {f"{p.strip()}_" for p in cases.split(",") if p.strip()}
        dataset.cases = [c for c in dataset.cases if any((c.name or "").startswith(p) for p in prefixes)]
    return dataset


def model_id(model: str) -> str:
    return model.split(":", 1)[-1]


async def estimates(settings: Settings, dataset: EvalDataset) -> list[CostEstimate]:
    engine = create_engine_for(settings.database_url)
    try:
        async with create_session_factory(engine)() as session:
            measured = await baseline_from_usage_logs(session)
    finally:
        await engine.dispose()
    fast, guard = model_id(settings.agent_model_fast), model_id(settings.agent_model_guard)
    baselines = [b for b in (measured, MEASURED_LOW, MEASURED_HIGH) if b is not None]
    return [estimate_cost(dataset, b, model=fast, guard_model=guard) for b in baselines]


def print_estimates(found: list[CostEstimate]) -> None:
    for e in found:
        print(
            f"- {e.baseline.source}: 사례 {e.cases}개 × 입력 {e.baseline.input_tokens_per_case:,} + "
            f"출력 {e.baseline.output_tokens_per_case:,} 토큰, 감시 {e.guard_calls}회 → "
            f"약 ${e.total_usd:.4f} (메인 ${e.agent_usd:.4f} + 감시 ${e.guard_usd:.4f}, 모델 {e.model})"
        )


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


async def evaluate(settings: Settings, dataset: EvalDataset) -> Any:
    url = eval_database_url(settings.database_url)
    create_eval_database(url)
    engine = create_engine_for(url)
    try:
        env = build_environment(eval_settings(settings, url), create_session_factory(engine))
        return await dataset.evaluate(case_task(env), name="myagent-eval", max_concurrency=1, progress=True)
    finally:
        await engine.dispose()
        drop_eval_database(url)


async def record(settings: Settings, user_id: int, report: Any) -> int:
    engine = create_engine_for(settings.database_url)
    try:
        async with create_session_factory(engine)() as session:
            return await record_eval_usage(session, user_id, [case.output for case in report.cases])
    finally:
        await engine.dispose()


def save_results(report: Any, stamp: str) -> Path:
    RESULTS_DIR.mkdir(exist_ok=True)
    rows = [
        {
            "name": case.name,
            "category": case.metadata.category,
            "assertions": {k: v.value for k, v in case.assertions.items()},
            "reasons": {k: v.reason for k, v in case.assertions.items() if v.reason},
            "output": case.output.model_dump(),
        }
        for case in report.cases
    ]
    failures = [{"name": f.name, "error": f.error_message} for f in report.failures]
    path = RESULTS_DIR / f"{stamp}.json"
    path.write_text(json.dumps({"cases": rows, "failures": failures}, ensure_ascii=False, indent=2), "utf-8")
    return path


async def run(args: argparse.Namespace, settings: Settings, dataset: EvalDataset) -> int:
    found = await estimates(settings, dataset)
    print(f"예상 비용(사례 {len(dataset.cases)}개):")
    print_estimates(found)
    if not args.run:
        print("\n실제로 돌리려면: --run --email 내이메일 --confirm-cost (모델 비용이 듭니다)")
        return 0
    if not (args.confirm_cost and args.email):
        raise SystemExit("--run에는 --email과 --confirm-cost가 함께 필요합니다.")
    if not settings.anthropic_api_key.get_secret_value():
        raise SystemExit(".env에 ANTHROPIC_API_KEY가 없습니다.")
    user_id = await owner_id(settings, args.email, max(e.total_usd for e in found) * 2)
    report = await evaluate(settings, dataset)
    report.print(include_input=False, include_output=False)
    written = await record(settings, user_id, report)
    stamp = datetime.now(SEOUL).strftime("%Y%m%d-%H%M")
    print(f"\n결과: {save_results(report, stamp)}  /  usage_logs(eval) {written}행")
    models = f"{model_id(settings.agent_model_fast)} / {model_id(settings.agent_model_smart)} / " \
        f"{model_id(settings.agent_model_guard)}"  # fmt: skip
    print(markdown_row(summarize(report), day=stamp[:8], models=models))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    dataset = selected(load_dataset(), args.cases)
    if args.run and DRAFT_MARK in DATASET_PATH.read_text(encoding="utf-8") and not args.allow_draft:
        raise SystemExit("dataset.yaml이 아직 초안입니다. 사례를 확인하고 맨 위 초안 표시를 지운 뒤 실행하세요.")
    return run_async(run(args, get_settings(), dataset))


if __name__ == "__main__":
    sys.exit(main())
