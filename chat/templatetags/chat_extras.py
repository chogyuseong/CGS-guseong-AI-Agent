import json
import re

from django import template
from django.utils import timezone
from django.utils.html import escape
from django.utils.safestring import mark_safe

register = template.Library()
BOLD = re.compile(r"\*\*(.+?)\*\*")


@register.filter
def light_markdown(text):
    """모델 답변의 **굵게**만 실제 굵은 글씨로 바꾼다.

    먼저 HTML을 모두 이스케이프한 뒤에 바꾸므로, 답변이나 도구 결과에 <script> 같은 것이 섞여 있어도
    실행되지 않는다(이스케이프된 글자 사이에서 ** 만 <strong>으로 바뀐다).
    """
    return mark_safe(BOLD.sub(r"<strong>\1</strong>", escape(text or "")))


@register.filter
def seconds_since(value):
    """그 시각부터 지금까지 몇 초 지났는지. 오래 기다리는 작업에 안내 문구를 띄울 때 쓴다.
    시각이 비어 있으면 아주 오래전으로 본다(화면이 오류로 깨지지 않게)."""
    if value is None:
        return 10**9
    return int((timezone.now() - value).total_seconds())


SUGGESTION_PREFIX = "[제안]"


@register.filter
def split_suggestions(content):
    """답변에서 '[제안]'으로 시작하는 줄을 떼어 낸다. (본문, [제안 문장들])

    자동 작업은 행동하지 않고 제안만 남긴다(절대 원칙 4). 화면은 제안을 버튼으로 그리고,
    누르면 그 문장이 일반 채팅 요청이 되어 승인 절차를 그대로 거친다.
    """
    body, suggestions = [], []
    for line in (content or "").splitlines():
        stripped = line.strip()
        if stripped.startswith(SUGGESTION_PREFIX):
            text = stripped[len(SUGGESTION_PREFIX) :].strip()
            if text:
                suggestions.append(text)
        else:
            body.append(line)
    text = "\n".join(body).strip()
    if suggestions:
        text = _drop_trailing_suggestion_heading(text)
    return text, suggestions


def _drop_trailing_suggestion_heading(text):
    """버튼 바로 위에 남는 '💡 제안', '**제안**' 같은 소제목 줄을 지운다(실사용에서 모델이 붙였다)."""
    lines = text.splitlines()
    if lines and lines[-1].strip(" *#:_💡-").strip() == "제안":
        lines = lines[:-1]
    return "\n".join(lines).strip()


@register.filter
def pretty_json(value):
    """단계 입력·출력을 사람이 읽기 좋은 JSON으로 보여 준다. 한글은 그대로 둔다."""
    if value is None:
        return "(없음)"
    return json.dumps(value, ensure_ascii=False, indent=2)
