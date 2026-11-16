"""
테스트 전용 설정. .env가 없어도 테스트가 돌도록 최소한의 값을 채운다.
테스트는 네트워크를 쓰지 않으므로 외부 API 키는 넣지 않는다.
"""

import os

os.environ.setdefault("SECRET_KEY", "test-only-not-a-real-secret")
os.environ.setdefault("DEBUG", "False")

from config.settings import *  # noqa: E402,F403

# 테스트 속도를 위해 비밀번호 해시를 가볍게 한다.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
