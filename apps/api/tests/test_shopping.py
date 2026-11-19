"""쇼핑 비교의 가격·옵션 판정 회귀 검사. 2026-11-18 공급자와 분리해 가상 판매처 자료로 검증한다."""

import json
from pathlib import Path

import pytest

from app.tools.shopping import Offer, ShoppingCriteria, compare_offers


@pytest.fixture
def offers():
    """실제 상품이나 특정 공급자 응답이 아닌 합성 판매 조건이다."""
    data = json.loads((Path(__file__).parent / "fixtures" / "shopping.json").read_text(encoding="utf-8"))
    return [Offer.model_validate(row) for row in data]


def criteria(**overrides):
    return ShoppingCriteria.model_validate(
        {
            "product": "KB100",
            "required": ["한글", "갈축"],
            "new_only": True,
            "include_shipping": True,
            "max_price": 120000,
            **overrides,
        }
    )


def test_shipping_and_options(offers):
    report = compare_offers(criteria(), offers)
    assert [row["판매처"] for row in report["조건 일치"]] == ["판매처 3"]
    assert report["조건 일치"][0]["비교 가격"] == "92,500원"
    assert [row["판매처"] for row in report["확인 필요"]] == ["판매처 2"]
    assert len(report["제외 이유"]) == 4


def test_display_price_and_budget(offers):
    report = compare_offers(criteria(include_shipping=False, max_price=88000), offers)
    assert [row["판매처"] for row in report["조건 일치"]] == ["판매처 2"]


def test_empty_and_no_match(offers):
    assert compare_offers(criteria(), [])["안내"] != compare_offers(criteria(max_price=100), offers)["안내"]


def test_option_absent_or_multiple(offers):
    for title in ["KB100 한글", "KB100 한글 갈축/청축"]:
        offer = offers[2].model_copy(update={"title": title})
        report = compare_offers(criteria(), [offer])
        assert not report["조건 일치"] and len(report["확인 필요"]) == 1


def test_conditional_price_and_unverified_purchase_terms(offers):
    """가격에 붙은 할인 자격과 배송 출처를 확인하지 못하면 추천에서 분리한다."""
    row = offers[2].model_copy(update={"title": "KB100 한글 갈축 카드할인 적용가"})
    assert compare_offers(criteria(), [row])["확인 필요"]
    assert compare_offers(criteria(excluded=["해외직구"]), [offers[2]])["확인 필요"]


@pytest.mark.parametrize("title", ["KB100 한글 미지원 갈축", "KB100 Pro 한글 갈축", "KB100 한글 무접점 (갈축 아님)"])
def test_similar_model_or_negated_option_is_not_verified(offers, title):
    row = offers[2].model_copy(update={"title": title})
    assert not compare_offers(criteria(), [row])["조건 일치"]


def test_unit_price_requires_one_clear_count(offers):
    """배송 포함 비교액에서 단가를 계산하고 묶음 선택이나 다중 수량은 추정하지 않는다."""
    request = criteria(product="생수", required=[], max_price=None)
    row = offers[2].model_copy(update={"title": "생수 2L 12병", "price": 9600, "shipping": 0})
    assert compare_offers(request, [row])["조건 일치"][0]["단가"] == "1병당 800원"
    for title in ("생수 6병/12병", "생수 12병 x 2", "생수 12병 2세트"):
        result = compare_offers(request, [row.model_copy(update={"title": title})])
        assert "단가" not in result["조건 일치"][0]


# ---- 2026-11-19(분석 26번): 배송비를 모를 때 예산을 넘지 않는 배송비 경계를 알려 준다 ----


def shipping_unknown(price: int) -> Offer:
    return Offer.model_validate(
        {
            "title": "KB100 한글 갈축 키보드",
            "store": "판매처 X",
            "url": "https://example.com/x",
            "price": price,
            "condition": "신품",
        }
    )


@pytest.mark.parametrize(
    ("price", "reason"),
    [
        (119_000, "배송비 미확인 · 배송비 1,000원 이하면 예산 충족"),
        (120_000, "배송비 미확인 · 무료배송일 때만 예산 충족"),
    ],
)
def test_unknown_shipping_names_the_budget_boundary(price, reason):
    report = compare_offers(criteria(), [shipping_unknown(price)])
    assert [row["이유"] for row in report["확인 필요"]] == [reason]


def test_unknown_shipping_without_budget_stays_plain():
    report = compare_offers(criteria(max_price=None), [shipping_unknown(119_000)])
    assert [row["이유"] for row in report["확인 필요"]] == ["배송비 미확인"]


def test_price_over_budget_is_still_excluded():
    report = compare_offers(criteria(), [shipping_unknown(121_000)])
    assert [row["이유"] for row in report["제외 이유"]] == ["예산 초과"]
