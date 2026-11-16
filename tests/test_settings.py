"""0단계: 모델 이름과 상한값이 코드가 아니라 설정에서 오는지 확인한다."""

from decimal import Decimal

from django.conf import settings


def test_agent_model_names_come_from_settings():
    for name in ["AGENT_MODEL_FAST", "AGENT_MODEL_SMART", "AGENT_MODEL_GUARD"]:
        assert getattr(settings, name)


def test_budget_defaults():
    assert settings.TASK_MAX_STEPS == 12
    assert settings.TASK_MAX_TOKENS == 60000
    assert settings.DAILY_COST_LIMIT_USD == Decimal("1.0")


def test_model_prices_cover_configured_models():
    for name in [settings.AGENT_MODEL_FAST, settings.AGENT_MODEL_SMART, settings.AGENT_MODEL_GUARD]:
        assert name in settings.MODEL_PRICES


def test_empty_database_url_falls_back_to_sqlite(monkeypatch):
    from config.settings import database_config

    monkeypatch.setenv("DATABASE_URL", "")
    assert database_config()["ENGINE"] == "django.db.backends.sqlite3"


def test_database_url_selects_postgres(monkeypatch):
    from config.settings import database_config

    monkeypatch.setenv("DATABASE_URL", "postgres://u:p@localhost:5432/agent")
    config = database_config()
    assert config["ENGINE"] == "django.db.backends.postgresql"
    assert config["NAME"] == "agent"


def test_time_zone_is_seoul():
    assert settings.TIME_ZONE == "Asia/Seoul"
    assert settings.USE_TZ is True
