"""환경변수 설정. 저장소 루트의 .env를 pydantic-settings로 읽는다.

- 키와 토큰은 SecretStr로 받아 실수로 로그에 찍혀도 '**********'로 보이게 한다.
- 이름은 소문자 필드지만 환경변수는 대소문자를 가리지 않으므로 DATABASE_URL이 database_url로 들어온다.
"""

from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# apps/api/app/config.py 기준으로 세 단계 위가 저장소 루트다.
REPO_ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class ModelPrice:
    """100만 토큰당 달러 가격. 프롬프트 길이에 따라 가격이 바뀌는 모델은 long_* 값을 쓴다."""

    input_per_mtok: Decimal
    output_per_mtok: Decimal
    # 한 요청의 입력 토큰이 이 값을 넘으면 long_* 가격을 쓴다(Haiku 5.5).
    long_prompt_threshold: int | None = None
    long_input_per_mtok: Decimal | None = None
    long_output_per_mtok: Decimal | None = None


# 모델별 가격. 키는 모델 응답에 찍히는 모델 이름(공급자 접두사 없음).
# 출처: https://platform.claude.com/docs/en/about-claude/pricing (2026-11-16 확인)
#       https://platform.claude.com/docs/en/about-claude/models/overview (2026-11-16 확인)
# - 기본 입력·출력 가격만 반영했다. 프롬프트 캐시, Batch API, US 전용 추론(1.1배)은 쓰지 않으므로 넣지 않았다.
# - 가격이 바뀌면 이 표만 고치면 된다. 표에 없는 모델은 비용 0으로 기록하고 경고를 남긴다(app/agent/budget.py).
MODEL_PRICES: dict[str, ModelPrice] = {
    # Claude Haiku 5.5: 프롬프트 10만 토큰 이하 $0.10 / $0.50, 초과 $0.50 / $2.50
    "claude-haiku-5-5": ModelPrice(
        input_per_mtok=Decimal("0.10"),
        output_per_mtok=Decimal("0.50"),
        long_prompt_threshold=100_000,
        long_input_per_mtok=Decimal("0.50"),
        long_output_per_mtok=Decimal("2.50"),
    ),
    # Claude Sonnet 5.5: $2 / $10 (1M 컨텍스트 전체가 같은 가격)
    "claude-sonnet-5-5": ModelPrice(input_per_mtok=Decimal("2"), output_per_mtok=Decimal("10")),
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=REPO_ROOT / ".env", env_file_encoding="utf-8", extra="ignore")

    # 필수: 없으면 서버가 시작되지 않는다.
    database_url: str
    session_secret: SecretStr
    app_origin: str

    # 실행 환경(BUILD_SPEC 12.2). production이면 APP_ORIGIN이 https여야 하고 쿠키에 늘 Secure를 붙인다.
    app_env: Literal["development", "test", "production"] = "development"

    # LLM. 모델 문자열은 Pydantic AI 형식('공급자:모델 ID')이다.
    # 모델 ID 출처: https://platform.claude.com/docs/en/about-claude/models/overview (2026-11-16 확인)
    anthropic_api_key: SecretStr = SecretStr("")
    agent_model_fast: str = "anthropic:claude-haiku-5-5"
    agent_model_smart: str = "anthropic:claude-sonnet-5-5"
    agent_model_guard: str = "anthropic:claude-haiku-5-5"

    # 사용량 상한
    daily_cost_limit_usd: Decimal = Decimal("1.0")
    task_max_requests: int = 12
    task_max_tokens: int = 60000
    # 5단계 보강: 보관 기간(BUILD_SPEC 5.2, docs/DATA_POLICY.md).
    # 끝난 작업의 모델 기록·단계 입력/출력·DBOS 기록 / 실행 영수증
    execution_retention_days: int = Field(default=7, ge=1)
    receipt_retention_days: int = Field(default=90, ge=1)
    # 모델 호출 한 번의 제한 시간(초). 넘으면 그 호출은 실패로 끝난다(1단계 보강, BUILD_SPEC 6.1).
    model_call_timeout_seconds: float = 60.0

    # 외부 도구 (4단계부터 사용)
    kakao_rest_api_key: SecretStr = SecretStr("")
    naver_client_id: str = ""
    naver_client_secret: SecretStr = SecretStr("")
    google_client_id: str = ""
    google_client_secret: SecretStr = SecretStr("")
    google_redirect_uri: str = ""
    token_encryption_key: SecretStr = SecretStr("")

    # 웹 푸시 (3단계부터 사용)
    vapid_public_key: str = ""
    vapid_private_key: SecretStr = SecretStr("")
    vapid_subject: str = ""

    # 관찰 (8단계). 비어 있으면 아무것도 보내지 않는다.
    logfire_token: SecretStr = SecretStr("")

    @model_validator(mode="after")
    def require_https_in_production(self) -> Self:
        if self.app_env == "production" and not self.app_origin.startswith("https://"):
            raise ValueError("APP_ENV=production이면 APP_ORIGIN은 https://로 시작해야 한다")
        return self

    @property
    def cookie_secure(self) -> bool:
        """APP_ORIGIN이 https일 때만 쿠키에 Secure를 붙인다(이유는 docs/DECISIONS.md)."""
        return self.app_origin.startswith("https://")


@lru_cache
def get_settings() -> Settings:
    # 필수 값은 .env에서 채워지므로 인자 없이 만든다.
    return Settings()  # pyright: ignore[reportCallIssue]
