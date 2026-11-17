"""관찰(8단계): Logfire(OpenTelemetry) 연결과 가리기 설정.

설치된 버전: logfire 5.6.0, pydantic-ai-slim 2.68.0, opentelemetry-instrumentation-fastapi 0.66b2
(각 패키지의 docstring과 소스로 확인. 문서: https://logfire.pydantic.dev/docs/ ,
https://pydantic.dev/docs/ai/logfire/ , https://logfire.pydantic.dev/docs/how-to-guides/scrubbing/).

원칙
1. TELEMETRY_ENABLED=true와 LOGFIRE_TOKEN이 함께 있을 때만 Logfire로 보낸다(8단계 보강, BUILD_SPEC 12.2).
   둘 중 하나라도 없으면 아무것도 밖으로 보내지 않는다(send_to_logfire=False, 내보내기 없음).
   환경변수 LOGFIRE_TOKEN·LOGFIRE_SEND_TO_LOGFIRE·LOGFIRE_FLEET·OTEL_EXPORTER_OTLP_*_ENDPOINT로도 켜지지 않는다
   (send_to_logfire·fleet를 명시하고, OTLP 주소는 설정하는 동안 숨긴다). 콘솔 출력도 끈다.
2. 내용은 기본으로 넣지 않는다. Pydantic AI 계측은 include_content=False라 프롬프트·지시문·모델 답변·
   도구 인자·도구 결과(메일 본문, 검색 결과, 일정 제목)가 스팬에 들어가지 않는다. 남는 것은 모양뿐이다:
   에이전트 이름, 모델 이름, 도구 이름, tool_call_id, 토큰 수, 걸린 시간, 오류 종류.
3. FastAPI 계측: 요청·응답 헤더(쿠키 포함)는 기록하지 않고(capture_headers=False), 엔드포인트 인자 값
   (메시지 본문, 로그인 비밀번호)을 남기는 로그를 만들지 않으며(request_attributes_mapper → None),
   주소의 쿼리 문자열(OAuth code·state 등)은 지운다(strip_query_hook).
   Google OAuth 콜백은 트레이스 자체를 만들지 않는다.
4. 예외 메시지는 남기지 않는다(무엇이 들었는지 모르는 글이다). 예외 종류와 코드 위치만 남긴다(hide_exception_details).
5. 그래도 새어 나온 값을 위해 Logfire 기본 가리기(password, secret, api key, session, cookie, token 이름 등)에
   이 앱의 키·토큰 모양(Anthropic 키, Google 토큰, Fernet 암호문, 카카오 헤더 등)을 더한다.

6. 3단계 보강: 앱 로그(app.* 로거)도 같은 규칙이다. 예외를 함께 남기는 로그(exc_info, logger.exception)는
   레코드를 만들 때 예외 메시지를 지우고 종류와 코드 위치만 남긴다(install_log_redaction). 외부 API 오류 문장,
   푸시 서버 응답, DB 오류에 사용자 글·외부 콘텐츠가 섞여 와도 서버 로그 파일에 남지 않게.

로그(logging 모듈)는 Logfire로 보내지 않는다(LogfireLoggingHandler를 붙이지 않음). 설치된 dbos 3.5.0에는
OTLP·OpenTelemetry 코드가 없다(8단계 보강에서 소스로 확인).
트레이스에서 작업 하나가 어떻게 보이는지는 docs/OBSERVABILITY.md.
"""

import logging
import os
import traceback
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import logfire
from fastapi import FastAPI
from opentelemetry.sdk.trace import SpanProcessor
from opentelemetry.trace import Span, Status, StatusCode

from app.config import Settings

SERVICE_NAME = "myagent-api"
HIDDEN_MESSAGE = "(가림: 예외 메시지는 트레이스에 남기지 않는다)"

# Logfire 기본 패턴에 더하는 가리기 패턴(대소문자 무시). 스팬 속성의 키와 문자열 값 모두에 적용되고,
# 걸리면 그 값 전체가 "[Scrubbed due to ...]"로 바뀐다.
EXTRA_SCRUB_PATTERNS: tuple[str, ...] = (
    r"sk-ant-",  # Anthropic API 키
    r"ya29\.",  # Google access token
    r"1//0",  # Google refresh token
    r"gAAAAA",  # Fernet 암호문(connections.encrypted_token)
    r"KakaoAK",  # 카카오 REST API 인증 헤더 값
    r"X-N(?:aver|CP)",  # 네이버 API 키 헤더 이름
    r"\bbearer\s",  # Authorization: Bearer ...
    r"vapid",
    r"oauth",
    r"encrypted[._ -]?token",
    r"encryption[._ -]?key",
    r"code[._ -]?verifier",
)
# Logfire가 send_to_logfire와 상관없이 OTLP 내보내기를 더하는 환경변수(설치된 logfire 5.6.0 config.py).
OTLP_ENDPOINT_VARIABLES: tuple[str, ...] = (
    "OTEL_EXPORTER_OTLP_ENDPOINT",
    "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
    "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT",
    "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT",
)
# 트레이스를 만들지 않는 주소(정규식, 전체 URL에 대해 검사). OAuth code가 쿼리로 오는 주소다.
EXCLUDED_URLS: tuple[str, ...] = (r"/api/connections/google/callback",)


def telemetry_sending(settings: Settings) -> bool:
    """TELEMETRY_ENABLED=true이고 토큰이 있을 때만 True. 둘 중 하나라도 없으면 아무것도 보내지 않는다."""
    return settings.telemetry_enabled and bool(settings.logfire_token.get_secret_value().strip())


def logfire_options(settings: Settings) -> dict[str, Any]:
    """logfire.configure()에 넘길 값. send_to_logfire는 늘 명시한다(LOGFIRE_SEND_TO_LOGFIRE 환경변수보다 우선)."""
    sending = telemetry_sending(settings)
    return {
        "send_to_logfire": sending,
        "token": settings.logfire_token.get_secret_value().strip() if sending else None,
        "service_name": SERVICE_NAME,
        "console": False,
        "inspect_arguments": False,
        # 브라우저가 보낸 trace 헤더를 이어받지 않는다(이 서버가 트레이스의 시작점이다).
        "distributed_tracing": False,
        # Logfire Fleet(LOGFIRE_FLEET·OTEL_OPAMP_* 환경변수로 켜지는 원격 관리)을 쓰지 않는다.
        "fleet": False,
        "scrubbing": logfire.ScrubbingOptions(extra_patterns=list(EXTRA_SCRUB_PATTERNS)),
        "advanced": logfire.AdvancedOptions(exception_callback=hide_exception_details),
    }


@contextmanager
def otlp_environment_hidden() -> Iterator[None]:
    """logfire.configure()가 도는 동안만 OTEL_EXPORTER_OTLP_*_ENDPOINT를 숨긴다.

    설치된 logfire 5.6.0은 send_to_logfire와 상관없이 이 환경변수가 있으면 그 주소로 가는 OTLP 내보내기
    (트레이스·지표·로그)를 더한다(logfire/_internal/config.py, 테스트로 확인). 이 앱이 보내는 곳은 Logfire 하나다.
    내보내기는 만들 때만 주소를 읽으므로 설정이 끝나면 환경변수를 그대로 되돌린다.
    """
    saved = {name: os.environ.pop(name) for name in OTLP_ENDPOINT_VARIABLES if name in os.environ}
    try:
        yield
    finally:
        os.environ.update(saved)


def configure_observability(settings: Settings, *, extra_span_processors: Sequence[SpanProcessor] = ()) -> bool:
    """Logfire를 설정하고 Pydantic AI를 내용 없이 계측한다. 밖으로 보내면 True.

    extra_span_processors: 테스트가 메모리 exporter를 붙일 때만 쓴다(전송 아님).
    여러 번 불러도 된다(마지막 설정이 이긴다). 테스트는 앱마다 create_app이 부른다.
    """
    options = logfire_options(settings)
    with otlp_environment_hidden():
        logfire.configure(**options, additional_span_processors=list(extra_span_processors) or None)
    # 모든 Agent(메인, 감시, 감시 판정)를 계측한다. 내용(프롬프트·답변·도구 인자와 결과)은 넣지 않는다.
    logfire.instrument_pydantic_ai(include_content=False, include_binary_content=False)
    return bool(options["send_to_logfire"])


def instrument_app(app: FastAPI) -> None:
    """FastAPI 요청마다 스팬 하나. 헤더·인자 값·쿼리 문자열은 남기지 않는다."""
    logfire.instrument_fastapi(
        app,
        capture_headers=False,
        request_attributes_mapper=no_request_attributes,
        excluded_urls=list(EXCLUDED_URLS),
        server_request_hook=strip_query_hook,
    )


def no_request_attributes(request: Any, attributes: dict[str, Any]) -> dict[str, Any] | None:
    """엔드포인트 인자 값(메시지 본문, 비밀번호 등)을 기록하는 로그를 만들지 않는다."""
    return None


def without_query(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def strip_query_hook(span: Span, scope: dict[str, Any]) -> None:
    """요청 스팬이 시작될 때 주소 속성에서 쿼리 문자열을 지운다(OAuth code·state, 검색어 등)."""
    if not span.is_recording():
        return
    attributes = getattr(span, "attributes", None) or {}
    for key in ("http.url", "url.full"):
        value = attributes.get(key)
        if isinstance(value, str):
            span.set_attribute(key, without_query(value))
    if "url.query" in attributes:
        span.set_attribute("url.query", "")


def frames_only(error: BaseException) -> str:
    """코드 위치(파일·줄·함수)와 예외 종류만. 예외 메시지는 넣지 않는다."""
    frames = "".join(traceback.format_list(traceback.extract_tb(error.__traceback__)))
    return f"Traceback (most recent call last):\n{frames}{type(error).__qualname__}: {HIDDEN_MESSAGE}"


APP_LOGGER_PREFIX = "app"


def redact_record_exception(record: logging.LogRecord) -> None:
    """앱 로그 레코드의 예외를 '종류 + 코드 위치'로 바꾼다. 포매터는 exc_text를 그대로 붙인다."""
    if not (record.name == APP_LOGGER_PREFIX or record.name.startswith(f"{APP_LOGGER_PREFIX}.")):
        return
    error = record.exc_info[1] if record.exc_info else None
    if error is not None:
        record.exc_text = frames_only(error)
        record.exc_info = None


def install_log_redaction() -> None:
    """레코드 공장을 감싸 앱 로그의 예외 메시지를 지운다. 여러 번 불러도 한 번만 감싼다."""
    current = logging.getLogRecordFactory()
    if getattr(current, "_myagent_redacting", False):
        return

    def factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
        record = current(*args, **kwargs)
        redact_record_exception(record)
        return record

    factory._myagent_redacting = True  # type: ignore[attr-defined]
    logging.setLogRecordFactory(factory)


def hide_exception_details(helper: Any) -> None:
    """Logfire가 예외를 기록하기 직전에 불린다(logfire.ExceptionCallbackHelper). 메시지를 지운다.

    예외 메시지에는 SQL 매개변수, 외부 API 응답 일부, 사용자 글이 들어갈 수 있다. 상태 설명에도 메시지가
    들어가지 않게 오류 상태를 설명 없이 먼저 정해 둔다(Logfire는 콜백이 정한 상태를 그대로 둔다).
    """
    helper.event_attributes["exception.message"] = HIDDEN_MESSAGE
    helper.event_attributes["exception.stacktrace"] = frames_only(helper.exception)
    if helper.level >= "error":
        helper.span.set_status(Status(StatusCode.ERROR))
