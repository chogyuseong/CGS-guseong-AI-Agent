import json

from django import template
from django.utils import timezone

register = template.Library()


@register.filter
def seconds_since(value):
    """그 시각부터 지금까지 몇 초 지났는지. 오래 기다리는 작업에 안내 문구를 띄울 때 쓴다."""
    return int((timezone.now() - value).total_seconds())


@register.filter
def pretty_json(value):
    """단계 입력·출력을 사람이 읽기 좋은 JSON으로 보여 준다. 한글은 그대로 둔다."""
    if value is None:
        return "(없음)"
    return json.dumps(value, ensure_ascii=False, indent=2)
