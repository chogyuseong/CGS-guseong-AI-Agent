"""복구 payload 암호화(7단계 보강, BUILD_SPEC 5.2 "메일 실행 기록: 필요한 복구 payload는 암호화").

키: PAYLOAD_ENCRYPTION_KEY(Fernet). DB와 분리된 .env·배포 비밀 설정에만 둔다(TOKEN_ENCRYPTION_KEY와 다른 키).
키가 없으면 메일 기능을 켜지 않는다(기능 플래그, app/agent/data_policy.py의 mail_enabled).

1. DBOS 직렬화기(EncryptedSerializer): DBOS 3.5.0은 설정의 serializer로 워크플로 입력·결과, 단계 결과,
   이벤트·메시지를 저장한다(dbos/_serialization.py, DBOSConfig.serializer). 기본값은 pickle → base64(평문과 같다).
   이 직렬화기는 pickle한 바이트를 Fernet으로 암호화한다. 그래서 dbos 스키마에는 사용자 메시지(워크플로 입력),
   이전 대화 기록(prepare 단계 결과), 모델 응답(모델 요청 단계 결과)이 모두 암호문으로만 남는다.
   - 직렬화 이름(SERIALIZER_NAME)이 각 행의 serialization 칸에 남는다. 키를 넣기 전의 행("py_pickle")은 DBOS가
     기본 직렬화기로 읽는다. 이름이 비어 있는 더 옛 행은 deserialize가 평문 pickle로 읽는다(암호문이 아니면).
   - 키를 바꾸거나 잃으면 끝나지 않은 워크플로를 복구할 수 없다(docs/DATA_POLICY.md 키 관리).
   - 오류 메시지·로그에 값(평문)을 넣지 않는다. DBOS 기본 직렬화기는 실패 때 값을 로그에 남긴다.
2. seal_json / open_json: 앱 DB(tasks.message_history)의 메일 도구 결과를 같은 키로 암호화·복호화한다
   (app/agent/history.py).

진입점: workflows.dbos_config가 payload_serializer를 DBOS 설정에 넣는다. 테스트 DBOS도 같은 직렬화기다
(tests/conftest.py TEST_PAYLOAD_KEY).

고칠 때 주의
- SERIALIZER_NAME을 바꾸면 이미 저장된 행의 이름과 달라져 DBOS 3.5.0이 그 행을 "Serialization ... is not
  available"(TypeError)로 거부한다(dbos/_serialization.py deserialize_value, 끝나지 않은 워크플로 복구 실패).
  바꿀 일이 있으면 진행 중인 작업이 없을 때 docs/OPERATIONS.md 3절 순서로.
- 키 교체·분실: docs/OPERATIONS.md 4절, docs/DATA_POLICY.md "키 관리". 키는 DB 백업과 다른 곳에도 보관한다.
- pickle.loads는 우리 DB가 쓴 값에만 쓴다(dbos 스키마는 이 앱만 쓴다). 다른 출처의 값을 이 함수로 풀지 않는다.
- 관련 테스트: tests/test_payload_crypto.py, tests/test_mail_privacy.py.
"""

import base64
import json
import pickle
from typing import Any

from cryptography.fernet import Fernet
from dbos import Serializer  # DBOSConfig.serializer의 타입(3.5.0 공개 API)

from app.config import Settings
from app.security import payload_cipher

# DBOS 행의 serialization 칸에 남는 이름. 저장된 행과 글자로 맞물리므로 바꾸지 않는다(위 "고칠 때 주의").
SERIALIZER_NAME = "myagent_fernet_pickle"
# Fernet 토큰(버전 0x80)의 base64 앞부분. 암호문과 옛 평문 pickle(base64)을 가르는 데 쓴다.
# 같은 접두사를 observability.EXTRA_SCRUB_PATTERNS도 가리기 패턴으로 쓴다.
FERNET_PREFIX = "gAAAAA"


class EncryptedSerializer(Serializer):
    """pickle → Fernet 암호화. DBOS가 시스템 테이블에 넣는 모든 값에 쓰인다."""

    def __init__(self, cipher: Fernet) -> None:
        self._cipher = cipher

    def serialize(self, data: Any) -> str:
        """pickle 바이트를 Fernet 암호문(ASCII)으로. 로그를 남기지 않는다(기본 직렬화기는 실패 때 값을 남긴다)."""
        return self._cipher.encrypt(pickle.dumps(data)).decode("ascii")

    def deserialize(self, serialized_data: str) -> Any:
        """암호문이면 풀어서, 이름 없는 옛 평문 행이면 그대로 읽는다.

        키가 바뀌었으면 cryptography.fernet.InvalidToken이 그대로 올라간다(그 워크플로는 복구할 수 없다).
        """
        if not serialized_data.startswith(FERNET_PREFIX):
            # 키를 넣기 전에 직렬화 이름 없이 저장된 옛 행(pickle → base64). 우리 DB가 쓴 값이다.
            return pickle.loads(base64.b64decode(serialized_data))  # noqa: S301
        return pickle.loads(self._cipher.decrypt(serialized_data.encode("ascii")))  # noqa: S301

    def name(self) -> str:
        return SERIALIZER_NAME


def payload_serializer(settings: Settings) -> EncryptedSerializer | None:
    """키가 있으면 DBOS 직렬화기. 없거나 Fernet 형식이 아니면 None(DBOS 기본 직렬화기, 메일 기능 꺼짐)."""
    cipher = payload_cipher(settings)
    return EncryptedSerializer(cipher) if cipher is not None else None


def seal_json(cipher: Fernet, value: Any) -> str:
    """JSON으로 바꿀 수 있는 값을 Fernet 암호문(ASCII)으로.

    모델 기록 속 메일 결과를 봉할 때 쓴다(history.seal_mail_returns).
    """
    return cipher.encrypt(json.dumps(value, ensure_ascii=False).encode("utf-8")).decode("ascii")


def open_json(cipher: Fernet, token: str) -> Any:
    """seal_json의 반대. 풀지 못하면(키가 바뀜) cryptography.fernet.InvalidToken.

    부르는 쪽(history.open_mail_returns)이 InvalidToken을 받아 "다시 읽어야 한다" 안내로 바꾼다.
    """
    return json.loads(cipher.decrypt(token.encode("ascii")))
