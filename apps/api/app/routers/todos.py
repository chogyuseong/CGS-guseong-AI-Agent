"""할 일 체크리스트 API. 화면의 '할 일' 탭이 쓴다.

    GET /api/todos          → 200 TodoListOut: 안 한 할 일 전부(오래된 것부터, 최대 200) + 최근 24시간 안에 완료한 것
    PUT /api/todos/{todo_id} → 200 TodoOut: {"is_done": true|false}로 체크·체크 해제 / 남의 것이거나 없으면 404

- 요구사항 밖 기능. 2026-11-18 사용자 결정(docs/DECISIONS.md "요구사항 밖 기능"). 장을 보며 산 것만 누르는 쓰임새라
  채팅을 거치지 않고 화면에서 바로 바꾼다. 할 일 추가는 지금처럼 채팅(add_todo)으로만 한다.
- 모든 조회·수정에 user_id 조건. 남의 할 일은 "없는 할 일"과 똑같이 404(존재 여부를 드러내지 않는다).
- PUT은 앱 전체 Origin 검사 미들웨어를 거친다(app/security.py).
- 할 일 바꾸기는 INTERNAL(앱 안에서만 바뀌고 되돌릴 수 있음)이라 승인을 거치지 않는다. 모델 호출도 없다.
- DB 작업은 모델 도구와 같은 함수(app/tools/notes.py mark_todo_done·mark_todo_open)를 쓴다.
  그래서 다시 체크해도 처음 완료한 시각(done_at)이 남는다.

고칠 때 주의:
- 경로·응답 모양을 바꾸면 gen:api 후 apps/web/lib/todos.ts를 고친다.
- DONE_VISIBLE_HOURS를 바꾸면 components/TodoChecklist.tsx의 안내 문구와 tests/test_todos_api.py를 같이 고친다.
- 관련 테스트: tests/test_todos_api.py, tests/test_tools.py(모델 도구 쪽).
"""

from datetime import timedelta

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app import clock
from app.db import DbSession
from app.models import Todo
from app.schemas.todos import TodoCheckIn, TodoListOut, TodoOut
from app.security import CurrentUser
from app.tools.notes import mark_todo_done, mark_todo_open

router = APIRouter(prefix="/todos", tags=["todos"])

# 완료한 할 일을 목록 아래에 줄 그어 남겨 두는 시간. 잘못 누른 체크를 되돌릴 여유를 주면서, 지난 장보기가
# 계속 쌓이지 않게 하루로 잡았다(2026-11-18 결정).
DONE_VISIBLE_HOURS = 24
# 한 화면에 그리는 최대 개수. 장보기·할 일 목록이 이보다 길어질 일은 드물다고 보고 잡은 값(넘으면 오래된 것부터 보임).
MAX_OPEN_TODOS = 200


# 안 한 것은 오래된 것부터(추가한 순서), 완료한 것은 방금 누른 것이 위로. 읽기만 한다.
@router.get("")
async def list_todos(user: CurrentUser, session: DbSession) -> TodoListOut:
    """할 일 체크리스트: 안 한 것 전부 + 최근 24시간 안에 완료한 것."""
    open_rows = await session.scalars(
        select(Todo)
        .where(Todo.user_id == user.id, Todo.is_done.is_(False))
        .order_by(Todo.created_at, Todo.id)
        .limit(MAX_OPEN_TODOS)
    )
    since = clock.now_utc() - timedelta(hours=DONE_VISIBLE_HOURS)
    done_rows = await session.scalars(
        select(Todo)
        .where(Todo.user_id == user.id, Todo.is_done.is_(True), Todo.done_at >= since)
        .order_by(Todo.done_at.desc(), Todo.id.desc())
    )
    return TodoListOut(todos=[TodoOut.model_validate(todo) for todo in [*open_rows, *done_rows]])


# 체크·체크 해제. 결과가 None이면 없거나 남의 할 일이라 404. 커밋은 notes.py 함수 안에서 한다(한 트랜잭션).
@router.put("/{todo_id}", responses={404: {"description": "없거나 남의 할 일"}})
async def check_todo(todo_id: int, body: TodoCheckIn, user: CurrentUser, session: DbSession) -> TodoOut:
    """할 일 체크(is_done=true) 또는 체크 해제(false). 이미 그 상태면 바꾸지 않는다."""
    if body.is_done:
        found = await mark_todo_done(session, user.id, todo_id)
        todo = found[0] if found is not None else None
    else:
        todo = await mark_todo_open(session, user.id, todo_id)
    if todo is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="할 일을 찾을 수 없습니다.")
    return TodoOut.model_validate(todo)
