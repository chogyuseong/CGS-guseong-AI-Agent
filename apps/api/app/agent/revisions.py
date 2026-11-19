"""'고쳐서 다시' 뒤 새 승인 카드가 이전 카드와 무엇이 달라졌는지 계산한다(2026-11-19, 분석 08번).

맡은 일: 두 카드의 고정 인자(steps.input = 실행 계획의 정규화 인자)를 비교해 바뀐 항목만 사람이 읽는 말로 돌려준다.
진입점: app/routers/approvals.py approvals_of_task(같은 작업에서 '고쳐서 다시'로 거절된 바로 앞 카드와 비교).
왜: 사용자가 "4시로 바꿔 줘"라고 했을 때 새 카드가 시각만 바꿨는지, 요청하지 않은 날짜·대상·장소까지 바꿨는지 카드에서
바로 확인할 수 있어야 한다(모델이 인자를 다시 만들기 때문에 다른 칸이 바뀔 수 있다). DB를 읽거나 쓰지 않는다.

고칠 때 주의:
- FIELD_LABELS는 일정 도구(app/tools/gcal.py) 인자 이름이다.
  새 EXTERNAL 도구를 만들면 여기에 넣는다(없으면 인자 이름 그대로).
- 응답 칸(field·before·after)은 schemas/tasks.py ApprovalChange와 화면 ApprovalCard가 쓴다. 바꾸면 gen:api.

변경 이력:
- 2026-11-19 처음 작성.
"""

import re
from datetime import datetime
from typing import Any

from app.schemas.tasks import ApprovalChange

FIELD_LABELS = {
    "event_id": "대상 일정",
    "title": "제목",
    "start": "시작",
    "end": "끝",
    "location": "장소",
    "description": "메모",
}
NONE_TEXT = "(없음)"
# 값 하나를 카드에 보일 최대 글자 수(긴 메모가 카드를 덮지 않게).
MAX_VALUE_CHARS = 60
LOCAL_MINUTE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$")
WEEKDAYS = "월화수목금토일"


def readable(value: Any) -> str:
    """'2026-11-20T15:00' → '11월 20일(금) 15:00', None·빈 값 → '(없음)', 그 밖은 글자로(60자까지)."""
    if value is None or value == "":
        return NONE_TEXT
    text = str(value)
    if LOCAL_MINUTE.match(text):
        moment = datetime.strptime(text, "%Y-%m-%dT%H:%M")
        return f"{moment.month}월 {moment.day}일({WEEKDAYS[moment.weekday()]}) {moment:%H:%M}"
    return text if len(text) <= MAX_VALUE_CHARS else text[:MAX_VALUE_CHARS] + "…"


def revision_changes(before: dict[str, Any], after: dict[str, Any]) -> list[ApprovalChange]:
    """바뀐 항목만, 이후 카드의 인자 순서대로(이전에만 있던 항목은 뒤에). 같으면 빈 목록."""
    keys = list(after) + [key for key in before if key not in after]
    return [
        ApprovalChange(
            field=FIELD_LABELS.get(key, key), before=readable(before.get(key)), after=readable(after.get(key))
        )
        for key in keys
        if readable(before.get(key)) != readable(after.get(key))
    ]
