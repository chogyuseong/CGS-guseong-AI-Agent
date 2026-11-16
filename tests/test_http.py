"""4단계: 외부 API 공통 호출. 타임아웃 10초, 재시도 1회, 실패는 한국어 문장으로 바꾼다."""

import httpx
import pytest

from tools import http


@pytest.fixture
def respond(monkeypatch):
    """가짜 HTTP 응답을 차례로 돌려준다. 네트워크를 쓰지 않는다."""
    calls = []

    def install(*responses):
        queue = list(responses)

        def handler(request):
            calls.append(request)
            item = queue.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

        monkeypatch.setattr(
            http, "make_client", lambda: httpx.Client(transport=httpx.MockTransport(handler), timeout=10)
        )
        return calls

    return install


def test_returns_json_on_success(respond):
    calls = respond(httpx.Response(200, json={"a": 1}))

    assert http.get_json("테스트", "https://api.example/x", params={"q": "성수동"}) == {"a": 1}
    assert calls[0].url.params["q"] == "성수동"


def test_timeout_is_retried_once_then_fails(respond):
    calls = respond(httpx.ReadTimeout("느림"), httpx.ReadTimeout("느림"))

    with pytest.raises(http.ExternalAPIError, match="10초"):
        http.get_json("날씨", "https://api.example/x")
    assert len(calls) == 2  # 처음 1번 + 재시도 1번


def test_timeout_then_success(respond):
    respond(httpx.ConnectTimeout("느림"), httpx.Response(200, json={"ok": True}))
    assert http.get_json("날씨", "https://api.example/x") == {"ok": True}


def test_server_error_is_retried_once(respond):
    calls = respond(httpx.Response(503), httpx.Response(200, json={"ok": True}))
    assert http.get_json("날씨", "https://api.example/x") == {"ok": True}
    assert len(calls) == 2


def test_401_means_bad_key_and_is_not_retried(respond):
    calls = respond(httpx.Response(401, json={"code": -401}))

    with pytest.raises(http.ExternalAPIError, match="키"):
        http.get_json("카카오", "https://api.example/x")
    assert len(calls) == 1


def test_429_means_rate_limited_and_is_not_retried(respond):
    calls = respond(httpx.Response(429))

    with pytest.raises(http.ExternalAPIError, match="한도"):
        http.get_json("네이버", "https://api.example/x")
    assert len(calls) == 1


def test_kakao_quota_error_400_minus_10_is_rate_limit(respond):
    # 카카오는 한도 초과를 429가 아니라 HTTP 400 + code -10으로 알린다(공식 에러 코드 문서).
    respond(httpx.Response(400, json={"code": -10, "msg": "API limit has been exceeded."}))

    with pytest.raises(http.ExternalAPIError, match="한도"):
        http.get_json("카카오", "https://api.example/x")


def test_other_client_error_mentions_status(respond):
    respond(httpx.Response(400, json={"error": True, "reason": "Latitude must be in range"}))

    with pytest.raises(http.ExternalAPIError, match="400"):
        http.get_json("날씨", "https://api.example/x")


def test_invalid_json_is_an_error(respond):
    respond(httpx.Response(200, content=b"<html>not json</html>"))

    with pytest.raises(http.ExternalAPIError):
        http.get_json("날씨", "https://api.example/x")


def test_writes_are_never_retried(respond):
    """쓰기 요청은 타임아웃이 나도 다시 보내지 않는다. 이미 반영됐다면 같은 일정이 두 번 생긴다."""
    calls = respond(httpx.ReadTimeout("느림"), httpx.Response(200, json={"id": "e1"}))

    with pytest.raises(http.ExternalAPIError, match="10초"):
        http.request_json("Google 캘린더", "POST", "https://api.example/events", json={"summary": "약속"})
    assert len(calls) == 1


def test_server_error_on_write_is_not_retried(respond):
    calls = respond(httpx.Response(503), httpx.Response(200, json={}))

    with pytest.raises(http.ExternalAPIError):
        http.request_json("Google 캘린더", "DELETE", "https://api.example/events/e1")
    assert len(calls) == 1


def test_empty_success_body_is_empty_dict(respond):
    respond(httpx.Response(204))
    assert http.request_json("Google 캘린더", "DELETE", "https://api.example/events/e1") == {}


def test_error_keeps_status_for_callers(respond):
    respond(httpx.Response(404, json={"error": {"code": 404}}))

    with pytest.raises(http.ExternalAPIError) as error:
        http.get_json("Google 캘린더", "https://api.example/events/missing")
    assert error.value.status == 404


def test_error_message_never_contains_api_key(respond):
    respond(httpx.Response(401))

    with pytest.raises(http.ExternalAPIError) as error:
        http.get_json("카카오", "https://api.example/x", headers={"Authorization": "KakaoAK secret-key-123"})
    assert "secret-key-123" not in str(error.value)
