"""7단계 보강: 복구 payload 암호화(PAYLOAD_ENCRYPTION_KEY, BUILD_SPEC 5.2 "필요한 복구 payload는 암호화").

1. DBOS 직렬화기: 워크플로 입력·단계 결과를 pickle한 뒤 Fernet으로 암호화한다. 저장 글자에 원문이 없고,
   예전(py_pickle, base64) 기록도 읽는다. 키가 없으면 DBOS 기본 직렬화기를 쓴다(그때는 메일 기능이 꺼진다).
2. 모델 기록(tasks.message_history)의 메일 도구 결과: 500자로 자른 뒤 암호화해 저장하고, 모델에게 줄 때만 푼다.
"""

import base64
import json
import pickle
from typing import Any

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart, UserPromptPart

from app.agent.history import history_for_storage, open_mail_returns
from app.agent.results import EXTERNAL_NOTICE, STORED_MAIL_CHARS, ToolResult
from app.agent.workflows import dbos_config
from app.config import Settings
from app.models.enums import TaskStatus
from app.payload_crypto import SERIALIZER_NAME, EncryptedSerializer, payload_serializer
from app.security import payload_cipher

SECRET = "MAILSECRET-4417 민지 생일 선물"
KEY = Fernet.generate_key().decode("ascii")


def cipher() -> Fernet:
    return Fernet(KEY.encode("ascii"))


# ---- 1. DBOS 직렬화기 ----


def test_serializer_round_trip_hides_plaintext() -> None:
    serializer = EncryptedSerializer(cipher())
    value = {"args": (7, f"요청 {SECRET}"), "kwargs": {}}

    stored = serializer.serialize(value)

    assert serializer.name() == SERIALIZER_NAME
    assert SECRET not in stored and "MAILSECRET" not in stored
    with pytest.raises(Exception):  # noqa: B017 - base64 pickle로는 풀리지 않는다(암호문)
        pickle.loads(base64.b64decode(stored))
    assert serializer.deserialize(stored) == value


def test_serializer_reads_legacy_plain_pickle() -> None:
    """키를 넣기 전에 기본 직렬화기로 저장한 기록(serialization 칸이 비어 있던 옛 행)도 읽는다."""
    legacy = base64.b64encode(pickle.dumps({"old": 1})).decode("ascii")

    assert EncryptedSerializer(cipher()).deserialize(legacy) == {"old": 1}


def test_serializer_with_other_key_fails_without_leaking() -> None:
    stored = EncryptedSerializer(cipher()).serialize(SECRET)
    other = EncryptedSerializer(Fernet(Fernet.generate_key()))

    with pytest.raises(Exception) as caught:  # noqa: B017
        other.deserialize(stored)
    assert SECRET not in str(caught.value)


def test_payload_cipher_and_dbos_config_follow_the_key(test_settings: Settings) -> None:
    test_settings.payload_encryption_key = SecretStr("")
    assert payload_cipher(test_settings) is None
    assert payload_serializer(test_settings) is None
    assert "serializer" not in dbos_config(test_settings)

    test_settings.payload_encryption_key = SecretStr("not-a-fernet-key")
    assert payload_cipher(test_settings) is None

    test_settings.payload_encryption_key = SecretStr(KEY)
    serializer = dbos_config(test_settings).get("serializer")
    assert isinstance(serializer, EncryptedSerializer)


# ---- 2. 모델 기록의 메일 결과 ----


def mail_messages(body: str) -> list[Any]:
    result = ToolResult.external({"id": "m1", "subject": "약속", "body": body})
    return [
        ModelRequest(parts=[UserPromptPart("메일 읽어줘")]),
        ModelResponse(parts=[ToolCallPart("read_mail", {"message_id": "m1"}, tool_call_id="c1")]),
        ModelRequest(parts=[ToolReturnPart("read_mail", result, tool_call_id="c1")]),
        ModelResponse(parts=[TextPart("요약입니다")]),
    ]


def mail_return(messages: list[Any]) -> Any:
    [part] = [p for m in messages for p in m.parts if isinstance(p, ToolReturnPart)]
    return part


def test_history_seals_mail_returns() -> None:
    stored = history_for_storage(
        [], mail_messages(f"{SECRET} " + "본문" * 3000), status=TaskStatus.DONE, cipher=cipher()
    )

    part = mail_return(stored)
    raw = json.dumps(part.content, ensure_ascii=False)
    assert SECRET not in raw and "MAILSECRET" not in raw
    assert part.content["data"]["notice"] == EXTERNAL_NOTICE  # 외부 콘텐츠 표시는 그대로(remember 검사)
    assert "sealed" in part.content["data"] and "content" not in part.content["data"]
    assert part.tool_call_id == "c1" and part.tool_name == "read_mail"  # 호출과 결과의 짝은 그대로

    opened = mail_return(open_mail_returns(stored, cipher()))
    content = opened.content["data"]["content"]
    assert SECRET in content  # 모델에게 줄 때는 풀어서 준다
    assert len(content) <= STORED_MAIL_CHARS  # 저장은 앞 500자만
    assert opened.content["data"]["truncated"] is True


def test_history_without_key_drops_mail_content() -> None:
    """키가 없으면(메일 기능이 꺼진 상태에서 남은 결과) 메일 내용을 평문으로 두지 않고 비운다."""
    stored = history_for_storage([], mail_messages(SECRET), status=TaskStatus.DONE, cipher=None)

    raw = json.dumps(mail_return(stored).content, ensure_ascii=False)
    assert SECRET not in raw
    assert "저장하지 않" in raw


def test_open_with_wrong_key_gives_placeholder() -> None:
    stored = history_for_storage([], mail_messages(SECRET), status=TaskStatus.DONE, cipher=cipher())

    opened = mail_return(open_mail_returns(stored, Fernet(Fernet.generate_key())))

    content = opened.content["data"]["content"]
    assert SECRET not in content
    assert "read_mail" in content  # 다시 읽으라는 안내


def test_non_mail_returns_are_untouched() -> None:
    result = ToolResult.external({"items": [SECRET]})
    messages = [
        ModelRequest(parts=[UserPromptPart("검색")]),
        ModelResponse(parts=[ToolCallPart("web_search", {"query": "x"}, tool_call_id="c1")]),
        ModelRequest(parts=[ToolReturnPart("web_search", result, tool_call_id="c1")]),
        ModelResponse(parts=[TextPart("끝")]),
    ]

    stored = history_for_storage([], messages, status=TaskStatus.DONE, cipher=cipher())

    assert SECRET in json.dumps(mail_return(stored).content, ensure_ascii=False, default=str)
