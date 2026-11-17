"""환경 구분(APP_ENV)과 테스트 DB 보호(BUILD_SPEC 12.2, 0단계)."""

import pytest
from pydantic import SecretStr, ValidationError

from tests.conftest import SettingsWithoutEnvFile
from tests.db_guard import UnsafeTestDatabaseError, check_test_database

BASE = {
    "database_url": "postgresql+psycopg://u:p@127.0.0.1:5432/myagent",
    "session_secret": SecretStr("s"),
}


def make(**values: object) -> SettingsWithoutEnvFile:
    return SettingsWithoutEnvFile(**{**BASE, **values})  # pyright: ignore[reportArgumentType]


def test_development_on_http_has_no_secure_cookie() -> None:
    settings = make(app_origin="http://localhost:3000")

    assert settings.app_env == "development"
    assert settings.cookie_secure is False


def test_production_requires_https_origin() -> None:
    with pytest.raises(ValidationError):
        make(app_env="production", app_origin="http://example.com")


def test_production_always_uses_secure_cookie() -> None:
    settings = make(app_env="production", app_origin="https://agent.example.com")

    assert settings.cookie_secure is True


@pytest.mark.parametrize(
    ("test_url", "app_url"),
    [
        # 이름이 _test로 끝나지 않는다
        ("postgresql+psycopg://u:p@localhost:5432/myagent", None),
        # 앱 DB와 같은 DB를 가리킨다
        ("postgresql+psycopg://u:p@db.example.com:5432/x_test", "postgresql+psycopg://u:p@db.example.com:5432/x_test"),
        # 로컬이 아닌 서버의 DB(운영 DB일 수 있다)
        ("postgresql+psycopg://u:p@db.example.com:5432/myagent_test", None),
    ],
)
def test_unsafe_test_database_is_refused(test_url: str, app_url: str | None) -> None:
    with pytest.raises(UnsafeTestDatabaseError):
        check_test_database(test_url, app_url)


def test_local_test_database_is_allowed() -> None:
    check_test_database(
        "postgresql+psycopg://u:p@127.0.0.1:5432/myagent_test",
        "postgresql+psycopg://u:p@127.0.0.1:5432/myagent",
    )
