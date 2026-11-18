"""배포 준비: 호스팅이 주는 DATABASE_URL 형식을 psycopg(3) 드라이버 형식으로 바꾼다(docs/DEPLOY.md).

Railway·Render 등은 postgresql:// 또는 postgres://로 준다. 그대로 쓰면 SQLAlchemy가 설치되지 않은 psycopg2를 찾는다.
2026-11-18: BUILD_SPEC 12절의 로그 보호를 로컬 API 시작 명령에서도 검사한다.
"""

import json
from pathlib import Path

import pytest
from pydantic import SecretStr

from app.config import Settings


def settings_with(url: str) -> Settings:
    return Settings(database_url=url, session_secret=SecretStr("x" * 40), app_origin="http://localhost:3000")


@pytest.mark.parametrize(
    "given",
    ["postgresql://u:p@db.internal:5432/app", "postgres://u:p@db.internal:5432/app"],
)
def test_hosting_database_url_uses_psycopg_driver(given: str) -> None:
    assert settings_with(given).database_url == "postgresql+psycopg://u:p@db.internal:5432/app"


def test_explicit_driver_is_kept() -> None:
    url = "postgresql+psycopg://u:p@localhost:5432/myagent"
    assert settings_with(url).database_url == url


def test_local_api_disables_access_log() -> None:
    """개발 서버 재시작 뒤에도 Google 콜백 쿼리가 Uvicorn 접속 로그에 기록되지 않아야 한다."""
    package = Path(__file__).resolve().parents[3] / "package.json"
    command = json.loads(package.read_text("utf-8"))["scripts"]["api"]
    assert "--no-access-log" in command.split()
