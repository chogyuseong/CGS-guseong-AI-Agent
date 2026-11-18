"""메모와 할 일 도구 (앱 안의 DB만 바꾼다).

등급: list_todos, list_notes = READ / save_note, add_todo, complete_todo = INTERNAL (toolsets.py에서 정한다).
- 도구 함수는 짧게 두고, DB 작업은 아래의 insert_*/select_* 함수로 뺐다(테스트에서 바꿔 끼우기도 쉽다).
- 각 도구는 자기 세션을 열고 바로 커밋한다. 작업이 나중에 실패해도 이미 한 일은 DB에 남는다.
- 모든 조회와 수정에 user_id 조건을 건다.

진입점: 모델(toolsets.read_tools의 list_todos·list_notes, internal_tools의 save_note·add_todo·complete_todo).
예약·감시 task는 read_tools만 받으므로 조회 두 개만 쓸 수 있다. 외부 API 호출 없음.
근거: docs/DECISIONS.md 1단계(2026-11-16 도구 목록. list_notes는 명세에 없지만 메모를 다시 볼 방법으로 넣음,
AgentDeps.db는 세션 공장이라 도구마다 짧은 트랜잭션).

다른 도구 파일이 이 파일의 short()를 가져다 쓴다(automation.py, memories.py, gmail.py). 바꾸면 그쪽 문구도 바뀐다.

고칠 때 주의:
- 도구를 더하거나 이름을 바꾸면 toolsets 등록 목록, 아래 LABELS, data_policy.TOOL_POLICIES(tests/test_data_policy.py)를
  같이 고친다.
- list_todos 결과의 "todos" 키는 감시 숫자 조건 예시(automation.py NumberField 설명, "할 일 개수")가 가리키는 경로다.
  키를 바꾸면 이미 저장된 감시가 값을 못 읽는다.
- tests/test_agent_run.py가 "app.tools.notes.insert_todo"를 이름으로 바꿔 끼운다(예상 못 한 예외 처리 확인).
  이름을 바꾸면 그 테스트도 고친다.
- mark_todo_done·mark_todo_open은 할 일 화면 API(app/routers/todos.py)도 쓴다. 바꾸면 tests/test_todos_api.py도 돌린다.

변경 이력:
- 2026-11-18 할 일 체크리스트 화면용 mark_todo_open(체크 해제) 추가(요구사항 밖, 사용자 결정 DECISIONS 2026-11-18).
  체크 해제는 화면에서만 한다(모델 도구는 그대로 다섯 개).
"""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated, Any

from pydantic import Field
from pydantic_ai import RunContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.deps import AgentDeps
from app.agent.results import ToolResult
from app.models import Note, Todo

NoteContent = Annotated[str, Field(min_length=1, max_length=2000, description="저장할 메모 내용")]
TodoTitle = Annotated[str, Field(min_length=1, max_length=200, description="할 일 제목. 예: 우유 사기")]
ListLimit = Annotated[int, Field(ge=1, le=50, description="가져올 최대 개수")]


def todo_view(todo: Todo) -> dict[str, Any]:
    """모델에게 돌려줄 할 일 한 건. id를 넣어 모델이 complete_todo에 그대로 쓰게 한다."""
    return {"id": todo.id, "title": todo.title, "is_done": todo.is_done}


def note_view(note: Note) -> dict[str, Any]:
    """모델에게 돌려줄 메모 한 건(저장 시각은 ISO 문자열, UTC)."""
    return {"id": note.id, "content": note.content, "created_at": note.created_at.isoformat()}


# ---- DB 작업 ----


async def insert_note(session: AsyncSession, user_id: int, content: str) -> Note:
    """메모 한 행을 넣고 바로 커밋한다(한 트랜잭션). 커밋 뒤 refresh로 id·created_at을 채워 돌려준다."""
    note = Note(user_id=user_id, content=content)
    session.add(note)
    await session.commit()
    await session.refresh(note)
    return note


async def insert_todo(session: AsyncSession, user_id: int, title: str) -> Todo:
    """할 일 한 행을 넣고 바로 커밋한다(한 트랜잭션). 같은 제목이 있어도 막지 않는다."""
    todo = Todo(user_id=user_id, title=title)
    session.add(todo)
    await session.commit()
    await session.refresh(todo)
    return todo


async def select_todos(session: AsyncSession, user_id: int, include_done: bool) -> list[Todo]:
    """오래된 것부터 최대 50개(user_id 조건). 50은 도구 설명("최대 50개")과 맞춘 값이다. 읽기만 한다."""
    query = select(Todo).where(Todo.user_id == user_id)
    if not include_done:
        query = query.where(Todo.is_done.is_(False))
    rows = await session.scalars(query.order_by(Todo.created_at, Todo.id).limit(50))
    return list(rows)


async def select_notes(session: AsyncSession, user_id: int, limit: int) -> list[Note]:
    """최신 것부터 limit개(user_id 조건, limit은 ListLimit이 1~50으로 막는다). 읽기만 한다."""
    rows = await session.scalars(
        select(Note).where(Note.user_id == user_id).order_by(Note.created_at.desc(), Note.id.desc()).limit(limit)
    )
    return list(rows)


async def mark_todo_done(session: AsyncSession, user_id: int, todo_id: int) -> tuple[Todo, bool] | None:
    """할 일을 완료로 바꾼다. (할 일, 이미 완료였는지)를 돌려주고, 내 할 일이 아니면 None.

    이미 완료면 쓰지 않는다(done_at을 덮어쓰지 않아 처음 완료한 시각이 남는다). 바꿨을 때만 커밋한다(한 트랜잭션).
    done_at은 app.clock이 아니라 실제 시각이다(eval 고정 시각과 무관).
    """
    todo = await session.scalar(select(Todo).where(Todo.id == todo_id, Todo.user_id == user_id))
    if todo is None:
        return None
    was_done = todo.is_done
    if not was_done:
        todo.is_done = True
        todo.done_at = datetime.now(UTC)
        await session.commit()
    return todo, was_done


async def mark_todo_open(session: AsyncSession, user_id: int, todo_id: int) -> Todo | None:
    """완료한 할 일을 다시 안 한 것으로 돌린다(화면에서 잘못 누른 체크 되돌리기). 내 할 일이 아니면 None.

    done_at도 지운다(Todo 모델 규칙: 완료면 is_done과 done_at을 함께 채운다). 바꿨을 때만 커밋한다(한 트랜잭션).
    """
    todo = await session.scalar(select(Todo).where(Todo.id == todo_id, Todo.user_id == user_id))
    if todo is None:
        return None
    if todo.is_done:
        todo.is_done = False
        todo.done_at = None
        await session.commit()
    return todo


# ---- 도구 (모델이 부르는 함수) ----


# 아래 다섯은 모델에게 보이는 도구다. docstring이 도구 설명이므로 고치지 않고 필요한 설명은 # 주석으로 적는다.
# 제목·내용의 앞뒤 공백만 지우고 그대로 저장한다(모델이 고쳐 쓴 글도 사용자 글과 같은 user_private으로 취급).
async def save_note(ctx: RunContext[AgentDeps], content: NoteContent) -> ToolResult:
    """사용자가 적어 달라고 한 내용을 앱 안의 메모로 저장한다."""
    async with ctx.deps.db() as session:
        note = await insert_note(session, ctx.deps.user_id, content.strip())
    return ToolResult.success(note_view(note))


async def list_notes(ctx: RunContext[AgentDeps], limit: ListLimit = 10) -> ToolResult:
    """최근에 저장한 메모를 최신순으로 보여 준다."""
    async with ctx.deps.db() as session:
        notes = await select_notes(session, ctx.deps.user_id, limit)
    return ToolResult.success({"notes": [note_view(n) for n in notes]})


async def add_todo(ctx: RunContext[AgentDeps], title: TodoTitle) -> ToolResult:
    """앱 안의 할 일 목록에 할 일을 하나 추가한다. 구글 캘린더와는 관계없다."""
    async with ctx.deps.db() as session:
        todo = await insert_todo(session, ctx.deps.user_id, title.strip())
    return ToolResult.success(todo_view(todo))


async def list_todos(ctx: RunContext[AgentDeps], include_done: bool = False) -> ToolResult:
    """앱 안의 할 일 목록을 보여 준다. include_done이 true면 완료한 것도 포함한다(최대 50개)."""
    async with ctx.deps.db() as session:
        todos = await select_todos(session, ctx.deps.user_id, include_done)
    return ToolResult.success({"todos": [todo_view(t) for t in todos]})


# 내 할 일이 아닌 id도 "찾을 수 없음"으로만 답한다(남의 할 일이 있는지 알려 주지 않는다).
async def complete_todo(ctx: RunContext[AgentDeps], todo_id: int) -> ToolResult:
    """할 일 하나를 완료로 표시한다. todo_id는 list_todos나 add_todo 결과의 id를 쓴다."""
    async with ctx.deps.db() as session:
        found = await mark_todo_done(session, ctx.deps.user_id, todo_id)
    if found is None:
        return ToolResult.failure(f"id가 {todo_id}인 할 일을 찾을 수 없습니다.")
    todo, was_done = found
    return ToolResult.success({**todo_view(todo), "was_already_done": was_done})


# ---- 타임라인 문구 (step.label) ----


def short(value: object, limit: int = 30) -> str:
    """타임라인 문구에 넣을 값을 limit자로 자른다(넘으면 끝을 '…'로). 다른 도구 파일도 가져다 쓴다."""
    text = str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


LABELS: dict[str, Callable[[dict[str, Any]], str]] = {
    "save_note": lambda args: "메모 저장하는 중",
    "list_notes": lambda args: "메모 목록 확인하는 중",
    "add_todo": lambda args: f"할 일에 '{short(args.get('title', ''))}' 추가하는 중",
    "list_todos": lambda args: "할 일 목록 확인하는 중",
    "complete_todo": lambda args: f"할 일 #{args.get('todo_id', '?')} 완료 처리하는 중",
}
