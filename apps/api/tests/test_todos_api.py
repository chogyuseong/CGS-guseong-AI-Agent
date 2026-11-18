"""할 일 체크리스트 API 테스트(GET /api/todos, PUT /api/todos/{id}).

요구사항 밖 기능으로 2026-11-18 사용자가 넣기로 정했다(docs/DECISIONS.md "요구사항 밖 기능").
할 일은 채팅(add_todo)으로 만들고, 화면에서는 체크·체크 해제만 한다.
"""

from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Todo, User
from tests.conftest import UserFactory


async def add_todo(db_session: AsyncSession, user: User, title: str, *, done_hours_ago: float | None = None) -> Todo:
    """할 일 한 건을 바로 저장한다. done_hours_ago를 주면 그만큼 전에 완료한 것으로 만든다."""
    todo = Todo(user_id=user.id, title=title)
    if done_hours_ago is not None:
        todo.is_done = True
        todo.done_at = datetime.now(UTC) - timedelta(hours=done_hours_ago)
    db_session.add(todo)
    await db_session.commit()
    return todo


async def test_list_shows_open_then_recently_done(
    client: httpx.AsyncClient, db_session: AsyncSession, logged_in_user: User
) -> None:
    milk = await add_todo(db_session, logged_in_user, "우유 사기")
    eggs = await add_todo(db_session, logged_in_user, "달걀 사기")
    bread = await add_todo(db_session, logged_in_user, "빵 사기", done_hours_ago=1)
    await add_todo(db_session, logged_in_user, "지난주 장보기", done_hours_ago=30)

    response = await client.get("/api/todos")

    assert response.status_code == 200
    todos = response.json()["todos"]
    assert [t["id"] for t in todos] == [milk.id, eggs.id, bread.id]
    assert [t["is_done"] for t in todos] == [False, False, True]
    assert set(todos[0]) == {"id", "title", "is_done", "done_at", "created_at"}


async def test_check_and_uncheck(client: httpx.AsyncClient, db_session: AsyncSession, logged_in_user: User) -> None:
    milk_id = (await add_todo(db_session, logged_in_user, "우유 사기")).id

    checked = await client.put(f"/api/todos/{milk_id}", json={"is_done": True})
    unchecked = await client.put(f"/api/todos/{milk_id}", json={"is_done": False})

    assert checked.status_code == 200
    assert checked.json()["is_done"] is True
    assert checked.json()["done_at"] is not None
    assert unchecked.json()["is_done"] is False
    assert unchecked.json()["done_at"] is None
    db_session.expire_all()
    stored = await db_session.scalar(select(Todo).where(Todo.id == milk_id))
    assert stored is not None
    assert stored.is_done is False


async def test_checking_twice_keeps_first_done_time(
    client: httpx.AsyncClient, db_session: AsyncSession, logged_in_user: User
) -> None:
    bread = await add_todo(db_session, logged_in_user, "빵 사기", done_hours_ago=2)
    first = (await client.get("/api/todos")).json()["todos"][0]["done_at"]

    again = await client.put(f"/api/todos/{bread.id}", json={"is_done": True})

    assert again.json()["done_at"] == first


async def test_other_users_todo_is_404_and_hidden(
    client: httpx.AsyncClient, db_session: AsyncSession, make_user: UserFactory, logged_in_user: User
) -> None:
    other = await make_user("other@example.com", "password-other")
    theirs_id = (await add_todo(db_session, other, "남의 할 일")).id

    listing = (await client.get("/api/todos")).json()["todos"]
    response = await client.put(f"/api/todos/{theirs_id}", json={"is_done": True})

    assert listing == []
    assert response.status_code == 404
    db_session.expire_all()
    stored = await db_session.scalar(select(Todo).where(Todo.id == theirs_id))
    assert stored is not None
    assert stored.is_done is False


async def test_todos_require_login(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/todos")).status_code == 401
    assert (await client.put("/api/todos/1", json={"is_done": True})).status_code == 401
