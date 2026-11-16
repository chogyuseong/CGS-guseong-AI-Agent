"""
테스트 전용 설정. .env가 없어도 테스트가 돌도록 최소한의 값을 채운다.

외부 API 키는 .env에 진짜 값이 있어도 강제로 비운다. 가짜 LLM이나 가짜 도구를 깜빡한 테스트가
실제 과금 호출을 하지 않게 하기 위해서다. load_dotenv는 이미 있는 환경변수를 덮어쓰지 않으므로
settings를 불러오기 전에 빈 값으로 먼저 넣어 둔다.
"""

import os

SECRET_ENV_NAMES = [
    "ANTHROPIC_API_KEY",
    "KAKAO_REST_API_KEY",
    "NAVER_CLIENT_ID",
    "NAVER_CLIENT_SECRET",
    "GOOGLE_CLIENT_ID",
    "GOOGLE_CLIENT_SECRET",
    "TOKEN_ENCRYPTION_KEY",
    "VAPID_PRIVATE_KEY",
]
for name in SECRET_ENV_NAMES:
    os.environ[name] = ""

os.environ.setdefault("SECRET_KEY", "test-only-not-a-real-secret")
os.environ.setdefault("DEBUG", "False")

from config.settings import *  # noqa: E402,F403

# 테스트 속도를 위해 비밀번호 해시를 가볍게 한다.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
