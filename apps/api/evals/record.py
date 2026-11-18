"""eval 실행 기록(8단계 보강, BUILD_SPEC 8단계 "날짜·모델·프롬프트 해시·코드 버전·데이터셋 버전 기록").

결과 JSON과 docs/EVALS.md 표 한 줄에 같이 남긴다. 표본 수(사례 수)와 실행 횟수(repeat)도 함께 둔다.
- 프롬프트 해시: 시스템 프롬프트(app/agent/prompts.py SYSTEM_PROMPT)의 sha256. 앞 8자리가 '프롬프트 버전'.
- 코드 버전: git 커밋과 커밋하지 않은 변경이 있었는지(dirty). git을 못 부르면 'unknown'.
- 데이터셋 버전: 사례 파일(dataset.yaml)과 세상을 만드는 코드(cases.py, fake_externals.py), 녹화본 fixture의
  내용 해시 앞 12자리. 사례 문장·기대·가짜 응답 중 하나라도 바뀌면 달라진다.

흐름 속 위치: run_evals.run이 맨 처음 build_run_record를 만들어 예상 비용 화면(print_preview), Dataset.evaluate의
metadata, summary의 보고·표 한 줄(markdown_row), 결과 JSON의 "run"에 같은 값을 쓴다.

고칠 때 주의
- 해시는 파일 바이트 전체다. cases.py·fake_externals.py는 주석만 고쳐도 데이터셋 버전이 바뀐다. 그러면 docs/EVALS.md
  결과 표에서 이전 실행과 같은 데이터셋으로 비교할 수 없으니, 바꾼 이유를 결과 표 아래에 적는다.
- WORLD_FILES에 파일을 더하거나 빼도 버전이 바뀐다. 사례의 세상을 만드는 새 파일(녹화본 등)이 생기면 여기에 넣는다.
- RunRecord 칸을 바꾸면 summary.record_lines·markdown_row, docs/EVALS.md '실행 기록' 표, tests/test_evals.py
  test_run_record_fields, tests/test_eval_rules.py를 같이 고친다.

변경 이력
- 2026-11-17 처음 작성(8단계 보강).
- 2026-11-18 주석 보강(동작 변경 없음).
- 2026-11-18 world_files(dataset_path): 요구사항 밖 데이터셋(extra_dataset.yaml)은 그 파일로 버전을 낸다.
"""

import hashlib
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from app.agent.prompts import SYSTEM_PROMPT
from app.clock import SEOUL
from app.config import REPO_ROOT, Settings
from evals.cases import DATASET_PATH, EVALS_DIR, EvalDataset
from evals.fake_externals import RECORDED, RECORDED_FILES

# git이 멈춰도 예상 비용 화면이 끝없이 기다리지 않게 하는 상한(초). 넘으면 코드 버전이 'unknown'으로 남는다.
GIT_TIMEOUT_SECONDS = 10
# 데이터셋 버전 해시에 들어가는 파일(순서도 해시에 영향). 사례 문장·기대(dataset.yaml), 세상의 모양(cases.py),
# 가짜 응답(fake_externals.py와 녹화본).
WORLD_FILES: tuple[Path, ...] = (
    DATASET_PATH,
    EVALS_DIR / "cases.py",
    EVALS_DIR / "fake_externals.py",
    *(RECORDED / name for name in RECORDED_FILES),
)


@dataclass(frozen=True)
class RunRecord:
    """한 번의 eval 실행을 다시 찾고 비교하기 위한 값(BUILD_SPEC 8단계 기록 항목). 결과 JSON의 "run"이 된다."""

    started_at: str  # 실행 시작(서울, ISO)
    eval_clock: str  # 사례가 고정한 '지금'(서로 다르면 쉼표로)
    models: dict[str, str]  # fast / smart / guard 모델 ID
    prompt_sha256: str
    prompt_version: str
    code_commit: str
    code_dirty: bool | None
    dataset_name: str
    dataset_version: str
    cases: int  # 표본 수
    repeat: int  # 사례마다 실행 횟수


def prompt_sha256() -> str:
    """시스템 프롬프트 고정 부분의 해시. 동적 지시문(날짜표·기억)은 들어가지 않는다."""
    return hashlib.sha256(SYSTEM_PROMPT.encode("utf-8")).hexdigest()


def world_files(dataset_path: Path = DATASET_PATH) -> tuple[Path, ...]:
    """버전 해시에 넣을 파일들. 데이터셋 파일만 바꿔 끼우고 세상 모양(cases.py·가짜 응답)은 같다."""
    return (dataset_path, *WORLD_FILES[1:])


def dataset_version(paths: Sequence[Path] = WORLD_FILES) -> str:
    """파일 이름과 내용 바이트로 만든 해시 앞 12자리. 파일 하나라도 없으면 예외가 난다(조용히 다른 버전을 내지 않게)."""
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.name.encode("utf-8") + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()[:12]


def run_git(*args: str) -> str | None:
    """저장소 루트에서 git을 부른다. 없거나 실패·시간 초과면 None(기록은 'unknown'으로 남고 실행은 계속된다)."""
    try:
        done = subprocess.run(
            ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, timeout=GIT_TIMEOUT_SECONDS, check=True
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout.strip()


def git_state() -> tuple[str, bool | None]:
    """(커밋, 커밋하지 않은 변경이 있는지). git을 못 부르면 ('unknown', None)."""
    commit = run_git("rev-parse", "HEAD")
    if not commit:
        return "unknown", None
    status = run_git("status", "--porcelain")
    return commit, None if status is None else bool(status)


def model_id(model: str) -> str:
    """설정의 'anthropic:모델ID'에서 공급자 접두어를 뗀다(가격표와 결과 표에 쓰는 이름)."""
    return model.split(":", 1)[-1]


def build_run_record(
    settings: Settings,
    dataset: EvalDataset,
    *,
    repeat: int,
    started_at: datetime | None = None,
    dataset_path: Path = DATASET_PATH,
) -> RunRecord:
    """지금 상태로 실행 기록을 만든다. 모델을 부르지 않는다(예상 비용만 볼 때도 같은 값을 보여 준다).

    부수 효과: git 명령 두 번(읽기만), 데이터셋 파일 읽기. eval_clock은 사례들의 now를 모은 것이다.
    """
    commit, dirty = git_state()
    digest = prompt_sha256()
    clocks = sorted({case.inputs.now.isoformat() for case in dataset.cases})
    return RunRecord(
        started_at=(started_at or datetime.now(SEOUL)).astimezone(SEOUL).isoformat(timespec="seconds"),
        eval_clock=", ".join(clocks),
        models={
            "fast": model_id(settings.agent_model_fast),
            "smart": model_id(settings.agent_model_smart),
            "guard": model_id(settings.agent_model_guard),
        },
        prompt_sha256=digest,
        prompt_version=digest[:8],
        code_commit=commit,
        code_dirty=dirty,
        dataset_name=dataset.name or "",
        dataset_version=dataset_version(world_files(dataset_path)),
        cases=len(dataset.cases),
        repeat=repeat,
    )
