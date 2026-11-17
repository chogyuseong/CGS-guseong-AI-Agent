"""ToolResult 구조(1단계 보강, BUILD_SPEC 2.3).

- status는 succeeded / failed / unknown. 조회 실패(failed)와 외부 변경 결과 불명(unknown)을 구분한다.
- 외부 API의 쓰기 요청이 시간 초과·연결 실패로 끝나면 상대 서버에 반영됐을 수 있으므로 unknown이다.
- 예전 모양({ok, error})으로 저장된 단계 결과도 읽는다(복구 재생).
"""

import httpx
import pytest

from app.agent.results import MAX_EXTERNAL_CHARS, ToolResult, ToolStatus
from app.tools.http import ExternalApiError, request_json


def test_success_failure_unknown_shapes() -> None:
    ok = ToolResult.success({"id": 1})
    failed = ToolResult.failure("할 일을 찾지 못했습니다.", error_code="not_found")
    unknown = ToolResult.unknown("반영되었는지 확인할 수 없습니다.", error_code="timeout")

    assert (ok.status, ok.ok) == (ToolStatus.SUCCEEDED, True)
    assert (failed.status, failed.ok, failed.error_code) == (ToolStatus.FAILED, False, "not_found")
    assert failed.user_message == failed.error == "할 일을 찾지 못했습니다."
    assert (unknown.status, unknown.ok, unknown.retryable) == (ToolStatus.UNKNOWN, False, False)


def test_serialized_shape_has_spec_fields() -> None:
    dumped = ToolResult.failure("실패", error_code="rejected", retryable=True).model_dump(mode="json")

    assert set(dumped) >= {
        "status", "data", "error_code", "user_message", "retryable",
        "operation_id", "receipt_id", "sources", "fetched_at", "truncated",
    }  # fmt: skip
    assert dumped["status"] == "failed"
    assert "ok" not in dumped  # ok는 status에서 계산하는 읽기 전용 속성이다


def test_legacy_stored_result_is_still_readable() -> None:
    """예전 모양으로 저장된 steps.output도 복구 재생 때 읽힌다."""
    assert ToolResult.model_validate({"ok": True, "data": 3}).status == ToolStatus.SUCCEEDED
    legacy_failure = ToolResult.model_validate({"ok": False, "error": "옛 실패"})
    assert legacy_failure.status == ToolStatus.FAILED
    assert legacy_failure.user_message == "옛 실패"


def test_external_marks_truncation() -> None:
    short = ToolResult.external({"title": "짧음"})
    long = ToolResult.external("가" * (MAX_EXTERNAL_CHARS + 10))

    assert short.truncated is False
    assert long.truncated is True


def transport(error: type[httpx.RequestError]) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        raise error("끊김", request=request)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_write_timeout_is_outcome_unknown() -> None:
    async with transport(httpx.ReadTimeout) as http:
        with pytest.raises(ExternalApiError) as caught:
            await request_json(http, "Google Calendar", "POST", "https://example.test/events", json={})

    assert caught.value.code == "timeout"
    assert caught.value.outcome_unknown is True
    result = ToolResult.from_api_error(caught.value)
    assert result.status == ToolStatus.UNKNOWN
    assert result.retryable is False  # 반영됐을 수 있으니 자동으로 다시 보내지 않는다


async def test_read_timeout_is_failed_and_retryable() -> None:
    async with transport(httpx.ConnectError) as http:
        with pytest.raises(ExternalApiError) as caught:
            await request_json(http, "날씨", "GET", "https://example.test/forecast")

    assert caught.value.code == "network"
    assert caught.value.outcome_unknown is False
    result = ToolResult.from_api_error(caught.value)
    assert (result.status, result.retryable, result.error_code) == (ToolStatus.FAILED, True, "network")


@pytest.mark.parametrize(
    ("status", "code", "retryable"),
    [(429, "rate_limited", True), (401, "unauthorized", False), (404, "rejected", False)],
)
async def test_http_error_codes(status: int, code: str, retryable: bool) -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(status))) as http:
        with pytest.raises(ExternalApiError) as caught:
            await request_json(http, "검색", "GET", "https://example.test/search")

    result = ToolResult.from_api_error(caught.value)
    assert (result.status, result.error_code, result.retryable) == (ToolStatus.FAILED, code, retryable)
