"""할 일 체크리스트 API 응답·요청(app/routers/todos.py). 웹은 이 모양을 OpenAPI에서 타입으로 생성한다.

화면 사용처: apps/web/lib/todos.ts, components/TodoChecklist.tsx.
요구사항 밖 기능, 2026-11-18 사용자 결정(docs/DECISIONS.md "요구사항 밖 기능").

고칠 때 주의: 모양을 바꾸면 gen:api 후 lib/todos.ts를 고치고 tests/test_todos_api.py의 키 목록을 맞춘다.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class TodoOut(BaseModel):
    """할 일 한 건. done_at은 처음 완료한 시각(UTC), 안 했으면 None."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    is_done: bool
    done_at: datetime | None
    created_at: datetime


class TodoListOut(BaseModel):
    """체크리스트: 안 한 것(오래된 것부터) 뒤에 최근 완료한 것(최근에 완료한 것부터)."""

    todos: list[TodoOut]


class TodoCheckIn(BaseModel):
    """체크(true) 또는 체크 해제(false)."""

    is_done: bool
