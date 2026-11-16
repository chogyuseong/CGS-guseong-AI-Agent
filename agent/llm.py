"""
LLM 호출을 한 곳으로 모은다.

루프는 LLMClient 인터페이스만 안다. 그래서 테스트에서는 FakeLLM을, 실제로는 AnthropicClient를
끼워 넣을 수 있다. 테스트가 네트워크와 API 요금 없이 돈다.
"""

import copy
from dataclasses import dataclass, field

from django.conf import settings

# 응답 하나의 최대 출력 토큰. 비스트리밍 요청에서 HTTP 타임아웃에 걸리지 않는 권장값.
MAX_OUTPUT_TOKENS = 16000
# 한 번의 API 호출 대기 시간(초). 재시도는 루프가 직접 한다.
REQUEST_TIMEOUT_SECONDS = 60


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict


@dataclass
class LLMResponse:
    """루프가 필요한 것만 담은 응답. SDK 객체를 루프 밖으로 새지 않게 한다."""

    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str = "end_turn"
    input_tokens: int = 0  # 캐시를 거치지 않은 입력
    output_tokens: int = 0
    cache_write_tokens: int = 0  # 이번에 캐시에 새로 쓴 입력(기본 입력보다 약 1.25배 비싸다)
    cache_read_tokens: int = 0  # 캐시에서 읽은 입력(기본 입력보다 훨씬 싸다)


class LLMError(Exception):
    """LLM 호출 실패. retryable이면 루프가 2초, 4초 뒤 다시 시도한다."""

    def __init__(self, message, retryable=True):
        super().__init__(message)
        self.retryable = retryable


class LLMNotConfigured(LLMError):
    """키나 가격 설정이 없어 호출 자체를 하면 안 되는 상태. 다시 시도해도 소용없다."""

    def __init__(self, message):
        super().__init__(message, retryable=False)


class LLMClient:
    def complete(self, system, messages, tools, model) -> LLMResponse:
        raise NotImplementedError


class FakeLLM(LLMClient):
    """정해 둔 응답을 차례로 돌려주는 가짜. 예외를 넣어 두면 그 차례에 예외를 던진다."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []  # 루프가 무엇을 보냈는지 테스트에서 확인하기 위해 기록한다

    def complete(self, system, messages, tools, model):
        self.calls.append(
            {"system": system, "messages": copy.deepcopy(messages), "tools": tools, "model": model}
        )
        if not self.responses:
            raise AssertionError("FakeLLM에 준비된 응답이 더 없습니다.")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class AnthropicClient(LLMClient):
    """공식 anthropic SDK로 Messages API의 tool use를 호출한다."""

    def __init__(self):
        if not settings.ANTHROPIC_API_KEY:
            raise LLMNotConfigured(
                "Claude API 키가 설정되지 않았습니다. "
                ".env의 ANTHROPIC_API_KEY를 채운 뒤 서버를 다시 켜 주세요."
            )
        import anthropic  # 키가 있을 때만 SDK를 불러온다

        self._anthropic = anthropic
        # 재시도는 루프가 명세대로(2초, 4초) 하므로 SDK 자동 재시도는 끈다. 둘이 겹치면 대기가 길어진다.
        self._client = anthropic.Anthropic(
            api_key=settings.ANTHROPIC_API_KEY, max_retries=0, timeout=REQUEST_TIMEOUT_SECONDS
        )

    def complete(self, system, messages, tools, model):
        _require_known_price(model)
        cached_system, cached_messages = with_cache_breakpoints(system, messages)
        request = {
            "model": model,
            "max_tokens": MAX_OUTPUT_TOKENS,
            "system": cached_system,
            "messages": cached_messages,
        }
        if tools:
            request["tools"] = tools
        try:
            response = self._client.messages.create(**request)
        except self._anthropic.APIError as error:
            raise _to_llm_error(self._anthropic, error) from error
        return _to_llm_response(response)


CACHE_MARKER = {"type": "ephemeral"}  # 기본 5분 유지. 같은 Task 안의 연속 호출과 가까운 요청이 다시 읽는다
PRICE_KEYS = ("input", "output", "cache_write", "cache_read")


def with_cache_breakpoints(system, messages):
    """프롬프트 캐시 표시를 두 군데에 붙인다. 원본은 바꾸지 않고 복사본을 돌려준다.

    1) 시스템 블록 끝: API는 도구 목록 -> 시스템 -> 메시지 순으로 읽으므로, 여기 표시 하나로
       도구 설명과 시스템 프롬프트가 함께 캐시된다. 같은 날의 모든 Task가 이 부분을 공유한다.
    2) 마지막 메시지의 마지막 블록: 에이전트는 한 Task 안에서 대화를 조금씩 늘려 가며 다시 보낸다.
       다음 호출은 지금까지의 대화를 캐시에서 싸게 읽고, 새로 붙은 Step만 정가로 읽는다.
    """
    cached_system = [{"type": "text", "text": system, "cache_control": CACHE_MARKER}]
    cached_messages = copy.deepcopy(messages)
    if cached_messages:
        last = cached_messages[-1]
        if isinstance(last["content"], str):
            last["content"] = [{"type": "text", "text": last["content"]}]
        last["content"][-1]["cache_control"] = CACHE_MARKER
    return cached_system, cached_messages


def _require_known_price(model):
    """가격을 모르면 하루 비용 상한을 지킬 수 없다. 그 상태로 돈이 나가는 호출은 하지 않는다."""
    price = settings.MODEL_PRICES.get(model) or {}
    if any(price.get(key) is None for key in PRICE_KEYS):
        raise LLMNotConfigured(
            f"settings.MODEL_PRICES에 {model} 가격이 비어 있어 비용 상한을 지킬 수 없습니다. "
            "가격을 채워 주세요."
        )


def _to_llm_response(response):
    # thinking 블록은 버린다. 루프가 대화를 DB에서 다시 조립하므로 다시 보내지 않는다(DECISIONS.md 참고).
    text = "".join(block.text for block in response.content if block.type == "text")
    tool_calls = [
        ToolCall(id=block.id, name=block.name, input=dict(block.input))
        for block in response.content
        if block.type == "tool_use"
    ]
    usage = response.usage
    return LLMResponse(
        text=text,
        tool_calls=tool_calls,
        stop_reason=response.stop_reason,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        # 캐시 토큰은 input_tokens와 따로 온다. 빼먹으면 비용이 실제보다 적게 계산된다.
        cache_write_tokens=usage.cache_creation_input_tokens or 0,
        cache_read_tokens=usage.cache_read_input_tokens or 0,
    )


def _to_llm_error(anthropic, error):
    """SDK 예외를 '다시 시도할 만한가' 기준으로 나눈다. 오류 원문은 로그용이고 화면에는 쓰지 않는다."""
    if isinstance(error, anthropic.AuthenticationError | anthropic.PermissionDeniedError):
        return LLMNotConfigured("Claude API 키가 올바르지 않거나 권한이 없습니다. 키를 확인해 주세요.")
    if isinstance(error, anthropic.RateLimitError | anthropic.APIConnectionError):
        return LLMError(str(error), retryable=True)
    if isinstance(error, anthropic.APIStatusError) and error.status_code >= 500:
        return LLMError(str(error), retryable=True)
    # 400, 404 같은 요청 오류는 같은 요청을 다시 보내도 같은 결과다.
    return LLMError(str(error), retryable=False)
