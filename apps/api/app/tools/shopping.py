"""채팅용 상품 비교. 확인된 판매 조건을 비교하며 DB나 외부 서비스에 쓰지 않는다.

2026-11-18 생활 비서 A단계. 고칠 때 주의: 한국어 결과 키는 채팅 카드와 test_shopping에서 사용한다.
"""

import re
import unicodedata
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, HttpUrl

Term = Annotated[str, Field(min_length=1, max_length=160, pattern=r"\S")]
Price = Annotated[int, Field(strict=True, gt=0)]


class Offer(BaseModel):
    """판매처별 단일 상품. 누락된 가격과 상태를 기본값으로 추정하지 않는다."""

    title: Term
    store: Term
    url: HttpUrl
    price: Price | None = None
    shipping: Annotated[int, Field(strict=True, ge=0)] | None = None
    condition: Literal["신품", "중고", "미확인"] = "미확인"


class ShoppingCriteria(BaseModel):
    """필수 옵션과 제외 항목은 별도로 검사한다. 금액 기준은 원화다."""

    product: Term
    required: Annotated[list[Term], Field(max_length=12)] = Field(default_factory=list)
    excluded: Annotated[list[Term], Field(max_length=12)] = Field(default_factory=list)
    new_only: bool = False
    include_shipping: bool = False
    max_price: Price | None = None


def normalized(value: str) -> str:
    """전각 문자와 공백을 정리하되 서로 다른 모델 번호는 합치지 않는다."""
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def contains(title: str, term: str) -> bool:
    """모델명과 수량 경계를 지켜 KB1000이나 12개를 KB100이나 2개로 오인하지 않는다."""
    return bool(re.search(r"(?<![a-z0-9])" + re.escape(normalized(term)) + r"(?![a-z0-9])", title))


# 복수 옵션을 함께 파는 상품에서는 최저 표시가가 선택 옵션 가격인지 알 수 없다.
OPTION_GROUPS = (("갈축", "청축", "적축", "흑축"), ("한글", "영문"))
ACCESSORIES = ("키캡", "키스킨", "보호필름", "케이스", "파우치", "교체용", "호환용")


def purchase_terms(criteria: ShoppingCriteria, title: str) -> str | None:
    """공급자가 별도 필드로 주지 않는 할인·직구 조건은 상품명에 없다는 이유로 충족 처리하지 않는다."""
    compact = title.replace(" ", "")
    conditions = ("카드할인", "카드발급", "쿠폰", "정기배송", "첫구매", "회원가", "와우회원")
    if any(word in compact for word in conditions):
        return "할인 적용 자격과 실제 결제 금액 확인 필요"
    for term in criteria.excluded:
        if any(word in normalized(term).replace(" ", "") for word in (*conditions, "직구", "구매대행", "해외")):
            return f"판매 조건 미확인 · {term}"
    return None


def classify(criteria: ShoppingCriteria, offer: Offer, total: int | None) -> tuple[str, str]:
    """배제 근거가 있으면 제외하고, 필수 정보가 비면 확인 후보로 남긴다."""
    title = normalized(offer.title)
    requested = normalized(" ".join([criteria.product, *criteria.required]))
    if not all(contains(title, part) for part in criteria.product.split()):
        return "제외 이유", "상품명 또는 모델 불일치"
    if any(contains(title, variant) and not contains(requested, variant) for variant in ("pro", "max", "mini", "plus")):
        return "확인 필요", "세부 모델명 확인 필요"
    excluded = [*criteria.excluded, *(word for word in ACCESSORIES if word not in requested)]
    if term := next((word for word in excluded if contains(title, word)), None):
        return "제외 이유", f"제외 항목 · {term}"
    if criteria.new_only and (offer.condition == "중고" or "중고" in title):
        return "제외 이유", "중고 상품"
    if criteria.max_price and offer.price and (total or offer.price) > criteria.max_price:
        return "제외 이유", "예산 초과"
    for term in criteria.required:
        if re.search(re.escape(normalized(term)) + r"\s*(?:미지원|미포함|아님|없음|제외)", title):
            return "제외 이유", f"필수 옵션 미포함 · {term}"
        for group in OPTION_GROUPS:
            if normalized(term) in group and any(contains(title, alt) for alt in group if alt != normalized(term)):
                if contains(title, term):
                    return "확인 필요", "여러 옵션의 가격이 함께 표시됨"
                return "제외 이유", f"다른 옵션 · 요청은 {term}"
        if not contains(title, term):
            return "확인 필요", f"필수 조건 미확인 · {term}"
    if criteria.new_only and offer.condition != "신품":
        return "확인 필요", "상품 상태 미확인"
    if offer.price is None:
        return "확인 필요", "상품 가격 미확인"
    if total is None:
        return "확인 필요", shipping_unknown_reason(criteria, offer)
    if reason := purchase_terms(criteria, title):
        return "확인 필요", reason
    return "조건 일치", "입력된 판매 조건이 일치합니다."


def shipping_unknown_reason(criteria: ShoppingCriteria, offer: Offer) -> str:
    """배송비를 모를 때의 이유. 배송비 포함 예산이 있으면 예산을 넘지 않는 배송비 경계를 알려 준다
    (2026-11-19, 분석 26번).

    "배송비 미확인"만 말하면 사용자가 상품 페이지에서 무엇을 확인해야 할지 모른다.
    상품가가 예산과 같으면 무료배송만 충족한다.
    상품가가 예산을 넘는 경우는 classify가 먼저 '예산 초과'로 제외한다.
    """
    if not criteria.max_price or offer.price is None:
        return "배송비 미확인"
    room = criteria.max_price - offer.price
    if room <= 0:
        return "배송비 미확인 · 무료배송일 때만 예산 충족"
    return f"배송비 미확인 · 배송비 {room:,}원 이하면 예산 충족"


def won(value: int | None) -> str:
    """무료 배송과 비용 미확인을 구분해 표시한다."""
    return "미확인" if value is None else f"{value:,}원"


def unit_price(title: str, total: int | None) -> str | None:
    """병·개·팩 수량 하나만 명시된 상품의 비교액을 나눈다. 묶음 배수·선택 옵션이면 표시하지 않는다."""
    title = normalized(title)
    if total is None or re.search(r"[×*+/~]|\d\s*x|x\s*\d|\d\s*(?:세트|박스|묶음|입)", title):
        return None
    counts = re.findall(r"(?<![\d.,-])(\d{1,5})\s*(병|개|팩)(?![가-힣])", title)
    if len(counts) != 1:
        return None
    count, unit = counts[0]
    if int(count) < 1:
        return None
    price, remainder = divmod(total, int(count))
    if remainder:
        return f"1{unit}당 약 {total / int(count):,.1f}원"
    return f"1{unit}당 {won(price)}"


def comparison_price(criteria: ShoppingCriteria, offer: Offer) -> int | None:
    """채팅 비교·자동 감시에서 같은 금액을 사용한다. 배송비가 불명인 합계는 None이다."""
    if not criteria.include_shipping:
        return offer.price
    return offer.price + offer.shipping if offer.price is not None and offer.shipping is not None else None


def compare_offers(criteria: ShoppingCriteria, offers: list[Offer]) -> dict[str, Any]:
    """그룹 안에서 확인된 가격순으로 비교한다. 서로 다른 판매처·옵션은 병합하지 않는다."""
    groups: dict[str, list[tuple[int | None, dict[str, str]]]] = {"조건 일치": [], "확인 필요": [], "제외 이유": []}
    for offer in offers:
        total = comparison_price(criteria, offer)
        group, reason = classify(criteria, offer, total)
        row = {
            "상품": offer.title,
            "판매처": offer.store,
            "표시가": won(offer.price),
            "배송비": won(offer.shipping),
            "비교 가격": won(total),
            "이유": reason,
            "링크": str(offer.url),
        }
        if per_unit := unit_price(offer.title, total):
            row["단가"] = per_unit
        groups[group].append((total if total is not None else offer.price, row))
    result = {}
    for group, rows in groups.items():
        rows.sort(key=lambda entry: (entry[0] is None, entry[0] or 0))
        result[group] = [row for _, row in rows]
    message = "확인한 상품 중 조건에 맞는 후보입니다." if result["조건 일치"] else "조건을 확인한 일치 상품이 없습니다."
    conditions = [criteria.product, *criteria.required]
    if criteria.new_only:
        conditions.append("신품")
    if criteria.max_price:
        conditions.append(f"{won(criteria.max_price)} 이하")
    if criteria.excluded:
        conditions.append("제외: " + ", ".join(criteria.excluded))
    return {
        "안내": message if offers else "비교할 상품이 없습니다.",
        "요청 조건": " · ".join(conditions),
        "가격 기준": "배송비 포함" if criteria.include_shipping else "상품 표시가",
        **result,
    }
