"""2026-11-18 생활 비서 A단계. 가짜 파트너스 응답에서 채팅 진입점과 공개 검색 범위를 검증한다."""

import hashlib
import hmac

import httpx
import pytest
from pydantic import SecretStr

from app.tools import coupang
from tests.fake_http import json_response
from tests.model_scripts import call, scripted, text
from tests.test_agent_run import send


@pytest.fixture
def keys(test_settings, monkeypatch):
    """카운터와 키를 매번 분리해 시간당 한도 검사에 다른 테스트가 영향을 주지 않게 한다."""
    test_settings.coupang_access_key = SecretStr("fixture-id")
    test_settings.coupang_secret_key = SecretStr("fixture-secret")
    monkeypatch.setattr(coupang, "LIMITER", coupang.SlidingWindowRateLimiter(10, 3600))


async def test_search_chat(client, agent, conversation, keys, fake_http):
    item = {
        "productName": "KB100 한글 갈축 신품",
        "productPrice": 90000,
        "productUrl": "https://link.coupang.com/a/example",
        "isFreeShipping": True,
    }
    fake_http.route("GET", coupang.SHOP_URL, json_response(200, {"rCode": "0", "data": {"productData": [item]}}))
    model = scripted(
        call("compare_products", product="KB100", required=["한글", "갈축"], include_shipping=True),
        text("조회한 상품을 비교했습니다."),
    )
    with agent.override(model=model.model()):
        task = await send(client, conversation, "KB100 한글 갈축 배송 포함 비교해줘")
    [step] = task["steps"]
    assert step["ok"] and step["risk_level"] == "READ"
    output = step["output"]
    assert output["fetched_at"] and output["sources"] == [item["productUrl"]]
    assert output["data"]["content"]["조건 일치"][0]["비교 가격"] == "90,000원"
    [request] = fake_http.sent("GET", coupang.SHOP_URL)
    assert request.url.params["keyword"] == "KB100"
    auth = request.headers["Authorization"]
    date = auth.split("signed-date=")[1].split(",")[0]
    message = date + "GET" + request.url.raw_path.decode().replace("?", "", 1)
    assert auth.endswith(hmac.new(b"fixture-secret", message.encode(), hashlib.sha256).hexdigest())


@pytest.mark.parametrize("status", [401, 403, 429])
async def test_access_errors(keys, fake_http, tool_deps, status):
    fake_http.route("GET", coupang.SHOP_URL, json_response(status, {}))
    with pytest.raises(coupang.ExternalApiError):
        await coupang.fetch_offers(tool_deps, "키보드")


async def test_timeout_no_retry(keys, fake_http, tool_deps):
    fake_http.route("GET", coupang.SHOP_URL, httpx.ReadTimeout("지연"))
    with pytest.raises(coupang.ExternalApiError):
        await coupang.fetch_offers(tool_deps, "KB100")
    assert len(fake_http.requests) == 1


async def test_invalid_response_is_not_empty(keys, fake_http, tool_deps):
    fake_http.route(
        "GET",
        coupang.SHOP_URL,
        json_response(200, {"rCode": "0", "data": {"productData": []}}),
        json_response(200, {"rCode": "0", "data": {}}),
    )
    assert await coupang.fetch_offers(tool_deps, "KB100") == []
    with pytest.raises(coupang.ExternalApiError):
        await coupang.fetch_offers(tool_deps, "KB100")


async def test_private_query_not_sent(client, conversation, agent, keys, fake_http):
    model = scripted(call("compare_products", product="다른 대화의 비공개 메모"), text("검색하지 않았습니다."))
    with agent.override(model=model.model()):
        task = await send(client, conversation, "KB100 찾아줘")
    assert not task["steps"][0]["ok"] and not fake_http.requests


async def test_missing_keys_and_local_limit_do_not_send(keys, tool_deps, fake_http):
    """최종 승인 키 미설정과 시간당 한도 초과는 모두 전송 전에 거절한다."""
    tool_deps.settings.coupang_secret_key = SecretStr("")
    with pytest.raises(coupang.ExternalApiError, match="최종 승인"):
        await coupang.fetch_offers(tool_deps, "키보드")
    tool_deps.settings.coupang_secret_key = SecretStr("fixture")
    fake_http.route("GET", coupang.SHOP_URL, json_response(200, {"rCode": "0", "data": {"productData": []}}))
    for _ in range(10):
        await coupang.fetch_offers(tool_deps, "키보드")
    with pytest.raises(coupang.ExternalApiError, match="시간당"):
        await coupang.fetch_offers(tool_deps, "키보드")
    assert len(fake_http.requests) == 10
