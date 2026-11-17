"""eval 실행 기록(8단계 보강, BUILD_SPEC 8단계 "날짜·모델·프롬프트 해시·코드 버전·데이터셋 버전 기록").

결과 JSON과 docs/EVALS.md 표 한 줄에 같이 남긴다. 표본 수(사례 수)와 실행 횟수(repeat)도 함께 둔다.
- 프롬프트 해시: 시스템 프롬프트(app/agent/prompts.py SYSTEM_PROMPT)의 sha256. 앞 8자리가 '프롬프트 버전'.
- 코드 버전: git 커밋과 커밋하지 않은 변경이 있었는지(dirty). git을 못 부르면 'unknown'.
- 데이터셋 버전: 사례 파일(dataset.yaml)과 세상을 만드는 코드(cases.py, fake_externals.py), 녹화본 fixture의
  내용 해시 앞 12자리. 사례 문장·기대·가짜 응답 중 하나라도 바뀌면 달라진다.
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

GIT_TIMEOUT_SECONDS = 10
WORLD_FILES: tuple[Path, ...] = (
    DATASET_PATH,
    EVALS_DIR / "cases.py",
    EVALS_DIR / "fake_externals.py",
    *(RECORDED / name for name in RECORDED_FILES),
)


@dataclass(frozen=True)
class RunRecord:
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
    return hashlib.sha256(SYSTEM_PROMPT.encode("utf-8")).hexdigest()


def dataset_version(paths: Sequence[Path] = WORLD_FILES) -> str:
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.name.encode("utf-8") + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()[:12]


def run_git(*args: str) -> str | None:
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
    return model.split(":", 1)[-1]


def build_run_record(
    settings: Settings, dataset: EvalDataset, *, repeat: int, started_at: datetime | None = None
) -> RunRecord:
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
        dataset_version=dataset_version(),
        cases=len(dataset.cases),
        repeat=repeat,
    )
