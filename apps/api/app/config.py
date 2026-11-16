"""환경변수 설정. 저장소 루트의 .env를 pydantic-settings로 읽는다.

- 키와 토큰은 SecretStr로 받아 실수로 로그에 찍혀도 '**********'로 보이게 한다.
- 이름은 소문자 필드지만 환경변수는 대소문자를 가리지 않으므로 DATABASE_URL이 database_url로 들어온다.
"""

from decimal import Decimal
from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# apps/api/app/config.py 기준으로 세 단계 위가 저장소 루트다.
REPO_ROOT = Path(__file__).resolve().parents[3]

# 모델별 가격(100만 토큰당 달러, (입력, 출력)). 값은 사용자가 공식 가격표를 보고 채운다.
# 예: "anthropic:모델이름": (Decimal("1.00"), Decimal("5.00"))
MODEL_PRICES: dict[str, tuple[Decimal, Decimal]] = {}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=REPO_ROOT / ".env", env_file_encoding="utf-8", extra="ignore")

    # 필수: 없으면 서버가 시작되지 않는다.
    database_url: str
    session_secret: SecretStr
    app_origin: str

    # LLM (1단계부터 사용). 모델 문자열은 Pydantic AI 형식이며 1단계에서 공식 문서로 확인한다.
    anthropic_api_key: SecretStr = SecretStr("")
    agent_model_fast: str = ""
    agent_model_smart: str = ""
    agent_model_guard: str = ""

    # 사용량 상한
    daily_cost_limit_usd: Decimal = Decimal("1.0")
    task_max_requests: int = 12
    task_max_tokens: int = 60000

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

    @property
    def cookie_secure(self) -> bool:
        """APP_ORIGIN이 https일 때만 쿠키에 Secure를 붙인다(이유는 docs/DECISIONS.md)."""
        return self.app_origin.startswith("https://")


@lru_cache
def get_settings() -> Settings:
    # 필수 값은 .env에서 채워지므로 인자 없이 만든다.
    return Settings()  # pyright: ignore[reportCallIssue]
