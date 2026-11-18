"""2026-11-18 생활 비서 A단계. 가짜 파트너스 응답에서 채팅 진입점과 공개 검색 범위를 검증한다."""

import hashlib
import hmac
from datetime import timedelta

import httpx
import pytest
from pydantic import SecretStr
from pydantic_ai.messages import ToolCallPart
from sqlalchemy import select

from app.automation.check import run_check
from app.models import Watch
from app.tools import coupang
from app.tools.automation import target_price
from tests.fake_http import json_response
from tests.model_scripts import call, scripted, text
from tests.test_agent_run import send
from tests.test_automation_runs import refreshed
from tests.test_watch_conditions import notices


def price_registration(product="KB100", **overrides):
    """프런트가 쓰는 채팅 진입점에서 실제 등록 도구와 숫자 조건을 호출한다."""
    fields = dict(
        description="키보드 목표가",
        condition_text="10만원 이하",
        interval_minutes=60,
        repeat=True,
        tool_name="coupang_price",
        tool_input={"product": product, "required": ["갈축"], "include_shipping": True},
        number_field="price",
        number_op="<=",
        number_value=100000,
    )
    return ToolCallPart("create_watch", fields | overrides)


async def test_price_watch_changes(
    client, agent, conversation, keys, fake_http, automation_services, db_session, logged_in_user
):
    model = scripted(price_registration(), text("감시를 등록했습니다."))
    with agent.override(model=model.model()):
        task = await send(client, conversation, "KB100 갈축 배송 포함 10만원 이하이면 계속 알려줘")
    assert task["steps"][0]["ok"]
    watch = (await db_session.scalars(select(Watch).where(Watch.user_id == logged_in_user.id))).one()
    start = watch.next_run_at
    for hour, price, free, status in [
        (0, 100000, True, "met"),
        (1, 99000, True, "met"),
        (2, 90000, False, "unknown"),
        (3, 120000, True, "not_met"),
        (24, 95000, True, "met"),
    ]:
        item = {
            "productName": "KB100 갈축",
            "productPrice": price,
            "productUrl": "https://link.coupang.com/a/test",
            "isFreeShipping": free,
        }
        fake_http.route("GET", coupang.SHOP_URL, json_response(200, {"rCode": "0", "data": {"productData": [item]}}))
        await run_check(automation_services, start + timedelta(hours=hour))
        watch = await refreshed(db_session, Watch, watch.id)
        assert watch.last_result and watch.last_result["status"] == status
        if hour == 2:
            assert watch.last_known_met is True
    alerts = [m for m in await notices(db_session, logged_in_user) if m.content.startswith("[조건 감시]")]
    assert len(alerts) == 2 and "95,000원" in alerts[-1].content
    assert alerts[-1].sources == ["https://link.coupang.com/a/test"]


@pytest.mark.parametrize(
    "command",
    [
        price_registration("비공개메모"),
        price_registration(number_field="다른값"),
        price_registration(number_op=">"),
        price_registration(number_value=19900.5),  # 원 단위가 아닌 금액
    ],
)
async def test_invalid_price_watch_not_saved(client, agent, conversation, keys, fake_http, db_session, command):
    with agent.override(model=scripted(command, text("등록하지 않았습니다.")).model()):
        task = await send(client, conversation, "KB100 갈축 10만원 이하 알림")
    assert not task["steps"][0]["ok"]
    assert await db_session.scalar(select(Watch.id)) is None and not fake_http.requests


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


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ({"field": "price", "op": "<=", "value": 100000}, 100000),
        ({"field": "price", "op": "<", "value": 95000.0}, 95000),
        ({"field": "price", "op": "<=", "value": True}, None),  # bool은 int의 하위형이지만 금액이 아니다
        ({"field": "price", "op": "<=", "value": 19900.5}, None),
        ({"field": "price", "op": "<=", "value": 0}, None),
        ({"field": "price", "op": "<=", "value": 2**53}, None),
        ({"field": "price", "op": ">=", "value": 100000}, None),
        ({"field": "rating", "op": "<=", "value": 100000}, None),
        ({"field": "price", "op": "<=", "value": "100000"}, None),
    ],
)
def test_target_price_accepts_only_whole_won_upper_bounds(spec, expected) -> None:
    assert target_price(spec) == expected
