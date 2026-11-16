"""사용량 화면의 숫자 표시. 호출 한 번이 1센트에 한참 못 미쳐서 달러는 소수 넷째 자리까지 보여 준다."""

from decimal import ROUND_HALF_UP, Decimal

from django import template

register = template.Library()

SMALLEST = Decimal("0.0001")


@register.filter
def usd(value, style=""):
    """Decimal 달러를 '$0.0430'처럼 소수 넷째 자리로 고정한다(장부에서 소수점 자리가 맞게).
    style="short"면 뒤의 0을 둘째 자리까지 줄인다('$1.00'). 상한처럼 정해진 금액에 쓴다.
    0보다 크지만 반올림하면 0이 되는 금액은 '<$0.0001'."""
    value = Decimal(value or 0)
    if value == 0:
        return "$0"
    if value < SMALLEST:
        return "<$0.0001"
    text = f"{value.quantize(SMALLEST, rounding=ROUND_HALF_UP):f}"
    if style == "short":
        whole, _, cents = text.partition(".")
        text = f"{whole}.{cents.rstrip('0').ljust(2, '0')}"
    return f"${text}"


@register.filter
def thousands(value):
    """정수에 천 단위 쉼표. django.contrib.humanize를 앱에 더하지 않으려고 직접 둔다."""
    return f"{int(value or 0):,}"


@register.filter
def percent(value):
    """0~100 비율을 '12.5%'로. 0보다 크지만 0.1 미만이면 '0.1% 미만'."""
    value = float(value or 0)
    if value == 0:
        return "0%"
    if value < 0.1:
        return "0.1% 미만"
    return f"{value:.1f}".removesuffix(".0") + "%"


@register.filter
def css_width(value):
    """막대의 style 너비. 로캘과 상관없이 소수점이 '.'이 되도록 직접 만든다."""
    return f"{float(value or 0):.2f}%"
