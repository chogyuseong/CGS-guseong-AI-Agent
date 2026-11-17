"""복구 payload 암호화(7단계 보강, BUILD_SPEC 5.2 "메일 실행 기록: 필요한 복구 payload는 암호화").

키: PAYLOAD_ENCRYPTION_KEY(Fernet). DB와 분리된 .env·배포 비밀 설정에만 둔다(TOKEN_ENCRYPTION_KEY와 다른 키).
키가 없으면 메일 기능을 켜지 않는다(기능 플래그, app/google_auth.py의 mail_enabled).

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
"""

import base64
import json
import pickle
from typing import Any

from cryptography.fernet import Fernet
from dbos import Serializer  # DBOSConfig.serializer의 타입(3.5.0 공개 API)

from app.config import Settings
from app.security import payload_cipher

SERIALIZER_NAME = "myagent_fernet_pickle"
FERNET_PREFIX = "gAAAAA"  # Fernet 토큰(버전 0x80)의 base64 앞부분


class EncryptedSerializer(Serializer):
    """pickle → Fernet 암호화. DBOS가 시스템 테이블에 넣는 모든 값에 쓰인다."""

    def __init__(self, cipher: Fernet) -> None:
        self._cipher = cipher

    def serialize(self, data: Any) -> str:
        return self._cipher.encrypt(pickle.dumps(data)).decode("ascii")

    def deserialize(self, serialized_data: str) -> Any:
        if not serialized_data.startswith(FERNET_PREFIX):
            # 키를 넣기 전에 직렬화 이름 없이 저장된 옛 행(pickle → base64). 우리 DB가 쓴 값이다.
            return pickle.loads(base64.b64decode(serialized_data))  # noqa: S301
        return pickle.loads(self._cipher.decrypt(serialized_data.encode("ascii")))  # noqa: S301

    def name(self) -> str:
        return SERIALIZER_NAME


def payload_serializer(settings: Settings) -> EncryptedSerializer | None:
    """키가 있으면 DBOS 직렬화기. 없으면 None(DBOS 기본 직렬화기, 메일 기능 꺼짐)."""
    cipher = payload_cipher(settings)
    return EncryptedSerializer(cipher) if cipher is not None else None


def seal_json(cipher: Fernet, value: Any) -> str:
    return cipher.encrypt(json.dumps(value, ensure_ascii=False).encode("utf-8")).decode("ascii")


def open_json(cipher: Fernet, token: str) -> Any:
    """풀지 못하면(키가 바뀜) cryptography.fernet.InvalidToken."""
    return json.loads(cipher.decrypt(token.encode("ascii")))
