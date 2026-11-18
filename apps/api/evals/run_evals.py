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

흐름: main(인자·초안 검사) → run → build_run_record(record) → estimates(estimate) → [--run이면] owner_id(하루 한도)
→ evaluate(database로 eval DB 만들기 → harness로 사례 실행 → evaluators 판정 → eval DB 지우기) → record_usage
→ summarize·render_summary·markdown_row(summary) → save_results.

근거: BUILD_SPEC 8단계·16절, DECISIONS 2026-11-17(실행 명령은 python -m evals.run_evals, 인자 없으면 예상 비용만,
--run --email --confirm-cost가 함께 있어야 실행, 초안 표시면 --run 거부, 오늘 비용 + 예상 상한 두 배 검사),
2026-11-18(사례 30개 확정, 초안 표시 제거).

고칠 때 주의
- 실제 실행은 비용이 든다. 테스트·점검에서 --run을 쓰지 않는다(AGENTS.md: 예상 비용을 알리고 사용자 확인 뒤에만).
- 안전 장치 1~3의 순서·조건을 바꾸면 docs/EVALS.md '실행 방법'과 AGENTS.md 실행 절의 eval 설명을 고친다.
- DRAFT_MARK 문구는 dataset.yaml·docs/EVALS.md에서 찾는 글자 그대로다. tests/test_evals.py에도 같은 글자가 있다.
- 결과 JSON(evals/results/)은 evals/results/.gitignore로 git에서 빠진다(docs/EVALS.md: 커밋하지 않음).

변경 이력
- 2026-11-17 처음 작성(8단계, --cases 포함). 같은 날 8단계 보강에서 실행 기록(record.py)·--repeat를 더함.
- 2026-11-18 주석 보강(동작 변경 없음).
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
from evals.cases import DATASET_PATH, EXTRA_DATASET_PATH, EvalDataset, load_dataset
from evals.database import create_eval_database, drop_eval_database, eval_database_url
from evals.estimate import MEASURED_HIGH, MEASURED_LOW, CostEstimate, baseline_from_usage_logs, estimate_cost
from evals.harness import build_environment, case_task, eval_settings, record_eval_usage
from evals.record import RunRecord, build_run_record, model_id
from evals.summary import EvalSummary, markdown_row, render_summary, summarize

# dataset.yaml에 이 글자가 있으면 --run을 거부한다(DECISIONS 2026-11-17). 사용자가 사례를 확인하기 전에
# 돈을 쓰고, 확인되지 않은 기대로 낸 숫자가 결과 표에 남는 것을 막는다.
DRAFT_MARK = "초안 — 사용자 확인 전"
RESULTS_DIR = Path(__file__).resolve().parent / "results"
# --repeat 상한. 비용이 회수에 비례한다(docs/EVALS.md 상한 가정 1회 약 $0.0665 → 5회 약 $0.33).
MAX_REPEAT = 5
# --dataset 이름 → 파일. spec16은 BUILD_SPEC 16절 30개(결과 표의 비교 기준), extra는 요구사항 밖 기능 사례
# (2026-11-18 사용자 결정, '고쳐서 다시' 등). 이름을 바꾸면 docs/EVALS.md 실행 방법도 고친다.
DATASETS = {"spec16": DATASET_PATH, "extra": EXTRA_DATASET_PATH}


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    """명령 인자. --repeat 범위 밖이면 argparse가 오류를 내고 끝낸다(모델을 부르기 전)."""
    parser = argparse.ArgumentParser(prog="python -m evals.run_evals", description="마이에이전트 eval")
    parser.add_argument("--run", action="store_true", help="실제 모델로 실행한다(비용이 든다)")
    parser.add_argument("--confirm-cost", action="store_true", help="예상 비용을 확인했다")
    parser.add_argument("--email", help="eval 비용을 기록할 계정(usage_logs, purpose=eval)")
    parser.add_argument("--cases", help="일부만: 사례 번호를 쉼표로(예: 01,17,29)")
    parser.add_argument("--repeat", type=int, default=1, help=f"사례마다 실행 횟수(1~{MAX_REPEAT}, 기본 1)")
    parser.add_argument("--allow-draft", action="store_true", help="초안 표시가 있어도 실행한다")
    parser.add_argument(
        "--dataset", choices=sorted(DATASETS), default="spec16", help="spec16(명세 30개, 기본) 또는 extra"
    )
    args = parser.parse_args(argv)
    if not 1 <= args.repeat <= MAX_REPEAT:
        parser.error(f"--repeat는 1~{MAX_REPEAT}입니다.")
    return args


def selected(dataset: EvalDataset, cases: str | None) -> EvalDataset:
    """--cases의 번호로 사례를 고른다. 사례 이름이 '01_…'처럼 번호와 밑줄로 시작한다는 dataset.yaml 규칙에 기댄다.

    받은 dataset을 그 자리에서 바꾼다. 맞는 번호가 없으면 빈 데이터셋이 된다(예상 비용 $0으로 보인다).
    """
    if cases:
        prefixes = {f"{p.strip()}_" for p in cases.split(",") if p.strip()}
        dataset.cases = [c for c in dataset.cases if any((c.name or "").startswith(p) for p in prefixes)]
    return dataset


async def estimates(settings: Settings, dataset: EvalDataset, repeat: int) -> list[CostEstimate]:
    """개발 DB 평균(있으면), 실측 하한, 실측 상한 세 가정의 예상 비용. 개발 DB는 읽기만 한다.

    메인 비용은 FAST 모델 가격, 감시는 GUARD 모델 가격으로 잡는다.
    """
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
    """비용을 기록할 계정. 오늘 비용 + 예상 상한이 하루 한도를 넘으면 거부한다.

    막는 사고: eval이 하루 한도(DAILY_COST_LIMIT_USD)를 다 써서 같은 날 그 계정의 실제 사용이 막히는 것.
    worst_usd는 run이 넘긴 '가장 큰 예상의 두 배'다(DECISIONS 2026-11-17).
    eval 실행 중에는 사례마다 예산 예약이 따로 걸리지만, 그 비용은 eval DB의 사용자에게 붙어 이 계정 한도와 무관하다.
    """
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
    """버리는 DB를 새로 만들고 모든 사례를 돌린 뒤, 성공·실패와 상관없이 DB를 지운다. 실제 모델을 부른다(비용).

    부수 효과: myagent_eval 생성·삭제(이름 검사는 database.py). 돌려주는 값은 Pydantic Evals EvaluationReport.
    """
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
    """개발 DB usage_logs에 purpose=eval 행을 남긴다(쓰기·커밋). eval DB는 이미 지워져 이 기록이 남는 유일한 비용이다.

    실행 오류로 끝난 사례(report.failures)는 출력이 없어 기록되지 않는다.
    """
    engine = create_engine_for(settings.database_url)
    try:
        async with create_session_factory(engine)() as session:
            return await record_eval_usage(session, user_id, [case.output for case in report.cases])
    finally:
        await engine.dispose()


def case_row(case: Any) -> dict[str, Any]:
    """결과 JSON의 사례 한 줄: 판정(assertions), 실패 이유(reasons), 걸린 시간, 출력 전체(답변 원문 포함)."""
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
    """결과 JSON: 실행 기록 + 요약 + 사례별 결과. 파일 이름은 실행 시작 시각(커밋하지 않음).

    부수 효과: evals/results/ 만들기, 파일 쓰기. 같은 분에 두 번 실행하면 앞 파일을 덮어쓴다.
    """
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
    """예상 비용을 먼저 보여 주고, --run과 확인 플래그가 모두 있을 때만 실제로 돌린다."""
    record = build_run_record(settings, dataset, repeat=args.repeat, dataset_path=DATASETS[args.dataset])
    found = await estimates(settings, dataset, args.repeat)
    print_preview(record, found)
    if not args.run:
        print("\n실제로 돌리려면: --run --email 내이메일 --confirm-cost (모델 비용이 듭니다)")
        return 0
    # 막는 사고: 예상 비용을 보지 않고 --run 하나로 돈을 쓰는 것, 비용을 붙일 계정 없이 하루 한도를 건너뛰는 것.
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
    """진입점. 초안 검사는 --run일 때만 한다(예상 비용 보기는 초안이어도 된다)."""
    args = parse_args(argv)
    path = DATASETS[args.dataset]
    dataset = selected(load_dataset(path), args.cases)
    # 막는 사고: 사용자가 확인하지 않은 사례(기대가 틀릴 수 있는 초안)로 돈을 쓰고 결과를 남기는 것.
    if args.run and DRAFT_MARK in path.read_text(encoding="utf-8") and not args.allow_draft:
        raise SystemExit(f"{path.name}이 아직 초안입니다. 사례를 확인하고 맨 위 초안 표시를 지운 뒤 실행하세요.")
    return run_async(run(args, get_settings(), dataset))


if __name__ == "__main__":
    sys.exit(main())
