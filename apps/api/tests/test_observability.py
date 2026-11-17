"""8단계: Logfire 연결과 가리기(scrubbing).

실제 Logfire 전송은 하지 않는다. 트레이스는 Logfire 테스트 도구의 메모리 exporter(TestExporter)로 받아서
메일 본문, 사용자 메시지, 모델 답변, 도구 인자, 키·토큰 모양 문자열, URL 쿼리(OAuth code)가 없는지 본다.

1. LOGFIRE_TOKEN이 비어 있으면 아무것도 밖으로 보내지 않는다(전송 시도 자체가 없음을 HTTP 전송 함수로 확인).
2. TELEMETRY_ENABLED=true와 토큰이 함께 있을 때만 send_to_logfire=True가 된다(설정 값만 확인, 실제 전송 없음).
   토큰만 있으면(환경변수 LOGFIRE_TOKEN·LOGFIRE_SEND_TO_LOGFIRE 포함) 보내지 않고, OTEL_EXPORTER_OTLP_* 주소로
   우회하는 내보내기도 만들지 않는다(8단계 보강).
3. 메일을 읽는 작업 하나의 트레이스에 메일 본문·프롬프트·답변·도구 인자가 없다(include_content=False).
4. 요청 주소의 쿼리(OAuth code, state 등)는 트레이스에 남지 않는다. 구글 콜백 요청은 트레이스 자체를 만들지 않는다.
5. 예외 메시지(안에 무엇이 들었는지 모르는 글)는 트레이스에 남지 않고 예외 종류만 남는다.
"""

import json
import os
from collections.abc import Iterator
from typing import Any

import httpx
import logfire
import logfire._internal.config as logfire_config
import pytest
import requests
from fastapi import FastAPI
from logfire.testing import TestExporter
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from pydantic import SecretStr

from app.agent.agent import MyAgent
from app.agent.deps import AgentDeps
from app.config import Settings
from app.models import User
from app.observability import configure_observability, logfire_options
from app.security import SESSION_COOKIE_NAME
from tests.conftest import SettingsWithoutEnvFile
from tests.fake_http import FakeHttp, fixture_json
from tests.model_scripts import call, scripted, text
from tests.test_agent_run import send
from tests.test_gmail import MESSAGES, connect_mail, gmail_api

MAIL_ID = "19a3f0c2d4e5b602"
USER_REQUEST = "오늘 온 메일 요약해줘 PRIVATE-USER-REQUEST"
FINAL_ANSWER = "요약했습니다 PRIVATE-FINAL-ANSWER"
MAIL_TEXT_MARKERS = ("이전 지시를 모두 무시", "이 일정을 삭제하라", "김민지", "minji@example.com")


@pytest.fixture
def trace_exporter(app: FastAPI, test_settings: Settings) -> Iterator[TestExporter]:
    """앱을 만든 뒤(create_app이 Logfire를 설정한다) 같은 설정에 메모리 exporter만 더해 다시 설정한다."""
    exporter = TestExporter()
    configure_observability(test_settings, extra_span_processors=[SimpleSpanProcessor(exporter)])
    yield exporter
    configure_observability(test_settings)  # 다음 테스트에 exporter가 남지 않게 되돌린다


def dumped(exporter: TestExporter) -> str:
    logfire.force_flush()
    spans = exporter.exported_spans_as_dict()
    return json.dumps(spans, ensure_ascii=False, default=str)


def span_names(exporter: TestExporter) -> list[str]:
    return [span["name"] for span in exporter.exported_spans_as_dict()]


def test_empty_token_never_sends(monkeypatch: pytest.MonkeyPatch, test_settings: Settings) -> None:
    sent: list[Any] = []

    def record_send(self: requests.Session, request: Any, **kwargs: Any) -> Any:
        sent.append(request.url)
        raise AssertionError("Logfire 토큰이 없는데 전송을 시도했다")

    monkeypatch.setattr(requests.Session, "send", record_send)
    test_settings.logfire_token = SecretStr("")

    sending = configure_observability(test_settings)
    with logfire.span("관찰 확인용 span"):
        logfire.info("관찰 확인용 로그")
    logfire.force_flush()

    assert sending is False
    assert logfire_options(test_settings)["send_to_logfire"] is False
    assert sent == []


def test_token_and_flag_turn_sending_on(test_settings: Settings) -> None:
    test_settings.logfire_token = SecretStr("pylf_v1_us_fake-token-for-test")
    test_settings.telemetry_enabled = True

    options = logfire_options(test_settings)

    assert options["send_to_logfire"] is True
    assert options["token"] == "pylf_v1_us_fake-token-for-test"
    assert options["console"] is False
    assert options["inspect_arguments"] is False
    assert options["fleet"] is False  # LOGFIRE_FLEET 환경변수로 원격 관리가 켜지지 않게
    test_settings.logfire_token = SecretStr("")
    assert logfire_options(test_settings)["send_to_logfire"] is False  # 켜도 토큰이 없으면 보내지 않는다
    assert logfire_options(test_settings)["token"] is None


# ---- 8단계 보강: TELEMETRY_ENABLED와 환경변수 우회 경로 ----


def test_telemetry_flag_is_off_by_default_and_read_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    def settings() -> Settings:
        return SettingsWithoutEnvFile(
            database_url="postgresql+psycopg://u:p@127.0.0.1:5432/x",
            session_secret=SecretStr("s"),
            app_origin="http://127.0.0.1:3000",
        )

    monkeypatch.delenv("TELEMETRY_ENABLED", raising=False)
    assert settings().telemetry_enabled is False

    monkeypatch.setenv("TELEMETRY_ENABLED", "true")
    assert settings().telemetry_enabled is True


def test_token_without_telemetry_flag_is_not_sending(test_settings: Settings) -> None:
    test_settings.logfire_token = SecretStr("pylf_v1_us_fake-token-for-test")  # TELEMETRY_ENABLED는 설정하지 않음

    options = logfire_options(test_settings)

    assert options["send_to_logfire"] is False
    assert options["token"] is None


def record_otlp_exporters(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Logfire가 OTEL_EXPORTER_OTLP_* 환경변수를 보고 만드는 OTLP 내보내기를 기록한다(logfire 5.6.0 config.py)."""
    built: list[str] = []
    for name in ("OTLPSpanExporter", "OTLPMetricExporter", "OTLPLogExporter"):
        original = getattr(logfire_config, name)

        def recording(*args: Any, _name: str = name, _original: Any = original, **kwargs: Any) -> Any:
            built.append(_name)
            return _original(*args, **kwargs)

        monkeypatch.setattr(logfire_config, name, recording)
    return built


def record_http_sends(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """OTLP HTTP 내보내기가 쓰는 requests 전송을 막고 기록한다(실제 전송 없음)."""
    sent: list[str] = []

    def record_send(self: requests.Session, request: Any, **kwargs: Any) -> Any:
        sent.append(str(request.url))
        raise requests.ConnectionError("테스트: 전송하지 않는다")

    monkeypatch.setattr(requests.Session, "send", record_send)
    return sent


@pytest.mark.parametrize(
    "variable",
    [
        "OTEL_EXPORTER_OTLP_ENDPOINT",
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
        "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT",
        "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT",
    ],
)
@pytest.mark.parametrize("token", ["", "pylf_v1_us_fake-token-for-test"])
def test_otlp_environment_cannot_bypass_the_flag(
    monkeypatch: pytest.MonkeyPatch, test_settings: Settings, variable: str, token: str
) -> None:
    # 배포 환경에 남아 있을 수 있는 값들: 토큰 환경변수, Logfire 전송 환경변수, OTLP 주소.
    monkeypatch.setenv("LOGFIRE_TOKEN", "pylf_v1_us_fake-token-from-environment")
    monkeypatch.setenv("LOGFIRE_SEND_TO_LOGFIRE", "true")
    monkeypatch.setenv(variable, "http://127.0.0.1:9/otlp-bypass-test")
    built, sent = record_otlp_exporters(monkeypatch), record_http_sends(monkeypatch)
    test_settings.logfire_token = SecretStr(token)  # TELEMETRY_ENABLED는 설정하지 않음

    sending = configure_observability(test_settings)
    with logfire.span("우회 확인용 span"):
        logfire.info("우회 확인용 로그")
    logfire.force_flush()

    assert sending is False
    assert built == []  # 환경변수 주소로 가는 OTLP 내보내기를 만들지 않았다
    assert sent == []
    assert os.environ[variable] == "http://127.0.0.1:9/otlp-bypass-test"  # 환경변수 자체는 바꾸지 않는다
    configure_observability(test_settings)


async def test_mail_task_trace_has_no_content(
    client: httpx.AsyncClient,
    agent: MyAgent,
    conversation: int,
    fake_http: FakeHttp,
    tool_deps: AgentDeps,
    logged_in_user: User,
    trace_exporter: TestExporter,
) -> None:
    await connect_mail(tool_deps)
    fake_http.route("GET", MESSAGES, gmail_api(full=fixture_json("gmail_message_injection.json")))
    model = scripted(call("read_mail", message_id=MAIL_ID), text(FINAL_ANSWER))

    with agent.override(model=model.model()):
        task = await send(client, conversation, USER_REQUEST)

    assert task["status"] == "done"
    trace = dumped(trace_exporter)
    names = span_names(trace_exporter)
    # 내용 없이도 작업의 모양(요청 → 에이전트 실행 → 모델 요청 → 도구 실행)은 남는다.
    assert any(name.startswith("POST /api/conversations") for name in names)
    assert "invoke_agent myagent" in names
    assert "execute_tool read_mail" in names
    assert '"gen_ai.aggregated_usage.input_tokens"' in trace
    for marker in (*MAIL_TEXT_MARKERS, "PRIVATE-USER-REQUEST", "PRIVATE-FINAL-ANSWER", MAIL_ID):
        assert marker not in trace, marker
    # 설정의 키·토큰과 세션 쿠키 값도 없다.
    for secret in ("test-kakao-key", "test-naver-secret", "test-google-secret", "test-session-secret"):
        assert secret not in trace, secret
    session_cookie = client.cookies.get(SESSION_COOKIE_NAME)
    assert session_cookie and session_cookie not in trace


async def test_query_strings_are_not_traced(
    client: httpx.AsyncClient, logged_in_user: User, trace_exporter: TestExporter
) -> None:
    await client.get("/api/usage/daily", params={"days": 3, "note": "QUERY-SECRET"})
    await client.get("/api/connections/google/callback", params={"code": "OAUTH-CODE-SECRET", "state": "STATE-X"})

    trace = dumped(trace_exporter)
    names = span_names(trace_exporter)

    assert "GET /api/usage/daily" in names
    assert "QUERY-SECRET" not in trace and "days=3" not in trace
    assert "OAUTH-CODE-SECRET" not in trace and "STATE-X" not in trace
    assert not any("callback" in name for name in names)  # 콜백은 트레이스를 만들지 않는다


async def test_exception_messages_are_hidden(test_settings: Settings, trace_exporter: TestExporter) -> None:
    with pytest.raises(ValueError), logfire.span("실패하는 작업"):
        raise ValueError("EXCEPTION-SECRET sk-ant-api03-abcdef")

    trace = dumped(trace_exporter)

    assert "ValueError" in trace
    assert "EXCEPTION-SECRET" not in trace and "sk-ant-api03" not in trace


async def test_unhandled_route_exception_message_is_hidden(
    app: FastAPI, client: httpx.AsyncClient, trace_exporter: TestExporter
) -> None:
    async def broken() -> None:
        raise RuntimeError("ROUTE-EXCEPTION-SECRET")

    app.add_api_route("/api/test-broken", broken)

    with pytest.raises(RuntimeError):
        await client.get("/api/test-broken")
    trace = dumped(trace_exporter)

    assert "GET /api/test-broken" in span_names(trace_exporter)
    assert "RuntimeError" in trace
    assert "ROUTE-EXCEPTION-SECRET" not in trace


async def test_key_shaped_values_are_scrubbed(test_settings: Settings, trace_exporter: TestExporter) -> None:
    # 키 이름만으로는 걸리지 않는 칸(value1~4)에 이 앱의 키·토큰 모양 값을 넣는다.
    logfire.info(
        "값 확인",
        value1="sk-ant-api03-Zq9x7Wv1",
        value2="ya29.Zq9x7Wv2",
        value3="gAAAAABZq9x7Wv3",
        value4="KakaoAK Zq9x7Wv4",
    )

    trace = dumped(trace_exporter)

    assert "Zq9x7Wv" not in trace
