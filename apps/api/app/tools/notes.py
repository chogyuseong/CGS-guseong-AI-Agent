"""메모와 할 일 도구 (앱 안의 DB만 바꾼다).

등급: list_todos, list_notes = READ / save_note, add_todo, complete_todo = INTERNAL (toolsets.py에서 정한다).
- 도구 함수는 짧게 두고, DB 작업은 아래의 insert_*/select_* 함수로 뺐다(테스트에서 바꿔 끼우기도 쉽다).
- 각 도구는 자기 세션을 열고 바로 커밋한다. 작업이 나중에 실패해도 이미 한 일은 DB에 남는다.
- 모든 조회와 수정에 user_id 조건을 건다.
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
    return {"id": todo.id, "title": todo.title, "is_done": todo.is_done}


def note_view(note: Note) -> dict[str, Any]:
    return {"id": note.id, "content": note.content, "created_at": note.created_at.isoformat()}


# ---- DB 작업 ----


async def insert_note(session: AsyncSession, user_id: int, content: str) -> Note:
    note = Note(user_id=user_id, content=content)
    session.add(note)
    await session.commit()
    await session.refresh(note)
    return note


async def insert_todo(session: AsyncSession, user_id: int, title: str) -> Todo:
    todo = Todo(user_id=user_id, title=title)
    session.add(todo)
    await session.commit()
    await session.refresh(todo)
    return todo


async def select_todos(session: AsyncSession, user_id: int, include_done: bool) -> list[Todo]:
    query = select(Todo).where(Todo.user_id == user_id)
    if not include_done:
        query = query.where(Todo.is_done.is_(False))
    rows = await session.scalars(query.order_by(Todo.created_at, Todo.id).limit(50))
    return list(rows)


async def select_notes(session: AsyncSession, user_id: int, limit: int) -> list[Note]:
    rows = await session.scalars(
        select(Note).where(Note.user_id == user_id).order_by(Note.created_at.desc(), Note.id.desc()).limit(limit)
    )
    return list(rows)


async def mark_todo_done(session: AsyncSession, user_id: int, todo_id: int) -> tuple[Todo, bool] | None:
    """할 일을 완료로 바꾼다. (할 일, 이미 완료였는지)를 돌려주고, 내 할 일이 아니면 None."""
    todo = await session.scalar(select(Todo).where(Todo.id == todo_id, Todo.user_id == user_id))
    if todo is None:
        return None
    was_done = todo.is_done
    if not was_done:
        todo.is_done = True
        todo.done_at = datetime.now(UTC)
        await session.commit()
    return todo, was_done


# ---- 도구 (모델이 부르는 함수) ----


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
    text = str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


LABELS: dict[str, Callable[[dict[str, Any]], str]] = {
    "save_note": lambda args: "메모 저장하는 중",
    "list_notes": lambda args: "메모 목록 확인하는 중",
    "add_todo": lambda args: f"할 일에 '{short(args.get('title', ''))}' 추가하는 중",
    "list_todos": lambda args: "할 일 목록 확인하는 중",
    "complete_todo": lambda args: f"할 일 #{args.get('todo_id', '?')} 완료 처리하는 중",
}
