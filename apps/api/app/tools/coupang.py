"""쿠팡 파트너스 상품 검색. 채팅 비교 도구에서만 호출한다(BUILD_SPEC 17절).

2026-11-18 국내 쇼핑 첫 공급자로 선택. 모델은 검색 범위만 추출하고 비교는 shopping.compare_offers가 한다.
고칠 때 주의: 가입 조건·시간당 한도는 docs/TOOLS.md, 서명과 응답 처리는 tests/test_coupang.py와 함께 본다.
"""

import hashlib
import hmac
import re
from datetime import UTC, datetime
from typing import Annotated, Any
from urllib.parse import urlencode

from pydantic import Field
from pydantic_ai import RunContext

from app.agent.deps import AgentDeps
from app.agent.results import ToolResult
from app.rate_limit import SlidingWindowRateLimiter
from app.tools.http import ExternalApiError, request_json
from app.tools.memories import chat_trigger_message
from app.tools.shopping import Offer, Price, ShoppingCriteria, Term, compare_offers, normalized

PATH = "/v2/providers/affiliate_open_api/apis/openapi/products/search"
SHOP_URL = "https://api-gateway.coupang.com" + PATH
# 파트너스 가이드 33쪽의 계정당 검색 한도. 같은 PC 프로세스의 모든 사용자·대화가 공유한다.
LIMITER = SlidingWindowRateLimiter(limit=10, window_seconds=3600)
Terms = Annotated[list[Term], Field(max_length=12, description="각각 따로 확인할 옵션이나 제외 단어")]


def signature(access: str, secret: str, query: str) -> str:
    """실제 전송할 인코딩된 쿼리와 UTC 시각에 서명한다. 키·서명은 요청 헤더에서만 사용한다."""
    date = datetime.now(UTC).strftime("%y%m%dT%H%M%SZ")
    message = (date + "GET" + PATH + query).encode()
    digest = hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()
    return f"CEA algorithm=HmacSHA256, access-key={access}, signed-date={date}, signature={digest}"


def offer_from_item(item: dict[str, Any]) -> Offer:
    """무료배송 true만 0원으로 해석한다. 로켓 표시만으로 배송비나 신품 상태를 추정하지 않는다."""
    name = item.get("productName")
    if not isinstance(name, str):
        raise ValueError("상품명 누락")
    used = any(word in name.replace(" ", "") for word in ("중고", "리퍼", "반품상품", "반품-", "신품급"))
    return Offer(
        title=name,
        store="쿠팡",
        url=item["productUrl"],
        price=item.get("productPrice"),
        condition="중고" if used else "신품" if any(s in name for s in ("신품", "새제품", "새상품")) else "미확인",
        shipping=0 if item.get("isFreeShipping") is True else None,
    )


async def fetch_offers(deps: AgentDeps, product: str) -> list[Offer]:
    """공개 상품명만 GET한다. 정상 빈 배열과 오류를 구분하고 공급자의 원시 오류 본문은 노출하지 않는다."""
    access = deps.settings.coupang_access_key.get_secret_value().strip()
    secret = deps.settings.coupang_secret_key.get_secret_value().strip()
    if not (access and secret):
        raise ExternalApiError(
            "쿠팡 파트너스 최종 승인 후 .env에 COUPANG_ACCESS_KEY와 COUPANG_SECRET_KEY를 설정해 주세요."
        )
    if not LIMITER.allow("partners"):
        raise ExternalApiError("쿠팡 상품 검색의 시간당 한도에 도달했습니다.", code="rate_limited")
    # 상위 5개만 비교해 원문과 출처가 기존 외부 콘텐츠 보관 상한에 맞도록 한다.
    query = urlencode({"keyword": product, "limit": 5})
    data = await request_json(
        deps.http,
        "쿠팡 파트너스",
        "GET",
        SHOP_URL + "?" + query,
        headers={"Authorization": signature(access, secret, query)},
        retry_read=False,
    )
    if not isinstance(data, dict) or str(data.get("rCode")) != "0":
        raise ExternalApiError("쿠팡 상품 조회가 거부됐습니다. 키와 상품 검색 권한을 확인해 주세요.")
    body = data.get("data")
    items = body.get("productData") if isinstance(body, dict) else None
    if not isinstance(items, list):
        raise ExternalApiError("쿠팡 상품 목록을 읽을 수 없습니다.", code="bad_response")
    try:
        return [offer_from_item(item) for item in items[:5]]
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        raise ExternalApiError("쿠팡 상품 정보를 확인할 수 없습니다.", code="bad_response") from error


async def public_product(deps: AgentDeps, product: str) -> bool:
    """현재 요청에 사용자가 직접 쓴 상품 이름만 내보낸다. 메일·일정 차단은 StepTracking도 검사한다."""
    if not re.fullmatch(r"[가-힣A-Za-z0-9][가-힣A-Za-z0-9 .+()_-]{0,79}", product):
        return False
    if re.search(r"\d(?:[- ]?\d){8,}|[A-Za-z0-9_-]{32,}", product):
        return False
    async with deps.db() as session:
        source = await chat_trigger_message(session, deps)
    return source is not None and normalized(product) in normalized(source.content)


async def compare_products(
    ctx: RunContext[AgentDeps],
    product: Term,
    required: Terms | None = None,
    excluded: Terms | None = None,
    new_only: bool = False,
    max_price: Price | None = None,
    include_shipping: bool = False,
) -> ToolResult:
    """쿠팡 상품 가격과 조건을 비교한다. product에는 사용자가 직접 쓴 공개 상품명·모델명만,
    required에는 필수 옵션, excluded에는 제외 조건을 넣는다. 신품 요청은 new_only, 원화 예산은 max_price,
    배송 포함 요청은 include_shipping에 반영한다. 미확인 조건을 충족했다고 말하지 않는다."""
    if not await public_product(ctx.deps, product):
        return ToolResult.failure("검색할 공개 상품명이나 모델명만 다시 알려 주세요.", error_code="data_policy")
    criteria = ShoppingCriteria(
        product=product,
        required=required or [],
        excluded=excluded or [],
        new_only=new_only,
        max_price=max_price,
        include_shipping=include_shipping,
    )
    try:
        offers = await fetch_offers(ctx.deps, product)
    except ExternalApiError as error:
        return ToolResult.from_api_error(error)
    content = compare_offers(criteria, offers)
    content["출처 안내"] = "쿠팡 파트너스 활동으로 일정액의 수수료를 제공받을 수 있습니다."
    return ToolResult.external(content, sources=[str(offer.url) for offer in offers])
