import json

from django import template

register = template.Library()


@register.filter
def pretty_json(value):
    """단계 입력·출력을 사람이 읽기 좋은 JSON으로 보여 준다. 한글은 그대로 둔다."""
    if value is None:
        return "(없음)"
    return json.dumps(value, ensure_ascii=False, indent=2)
