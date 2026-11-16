"""
OAuth 토큰 암호화. Connection.encrypted_token에는 이 모듈로 암호화한 문자열만 들어간다(절대 원칙 8).

Fernet(대칭키 암호화 + 위변조 검사)을 쓴다. 키는 .env의 TOKEN_ENCRYPTION_KEY 하나뿐이고,
키가 바뀌면 이전 토큰은 읽을 수 없으므로 사용자에게 다시 연결하라고 안내한다.
"""

import json

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings

from tools.http import ExternalAPIError


def _fernet():
    if not settings.TOKEN_ENCRYPTION_KEY:
        raise ExternalAPIError("TOKEN_ENCRYPTION_KEY가 설정되지 않아 연결 정보를 저장할 수 없습니다.")
    return Fernet(settings.TOKEN_ENCRYPTION_KEY.encode())


def encrypt_json(data):
    return _fernet().encrypt(json.dumps(data).encode()).decode()


def decrypt_json(token):
    try:
        return json.loads(_fernet().decrypt(token.encode()))
    except InvalidToken as error:
        raise ExternalAPIError(
            "저장된 연결 정보를 읽지 못했습니다. 연결 화면에서 다시 연결해 주세요."
        ) from error
