"""1단계 보강(BUILD_SPEC 9.1, 15절 10번): client_request_id로 메시지·task 중복 방지.

화면은 보낼 때마다 새 UUID를 만들고, 네트워크 오류로 다시 보낼 때는 같은 UUID를 쓴다.
서버는 (사용자, client_request_id)가 같으면 새 메시지·task를 만들지 않고 처음 task를 돌려준다.
"""

import uuid

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.agent import MyAgent
from app.models import Conversation, Message, Task, Todo
from tests.conftest import UserFactory, login
from tests.model_scripts import call, scripted, text


def url(conversation_id: int) -> str:
    return f"/api/conversations/{conversation_id}/messages"


async def count(session: AsyncSession, model: type[Message] | type[Task] | type[Todo]) -> int:
    return await session.scalar(select(func.count()).select_from(model)) or 0


async def test_resend_with_same_id_returns_same_task_and_adds_one_todo(
    client: httpx.AsyncClient, agent: MyAgent, conversation: int, db_session: AsyncSession
) -> None:
    """같은 요청이 두 번 와도 메시지·task·할 일은 하나다(우유 사기 추가 → todo 한 건)."""
    body = {"content": "우유 사기 할 일에 추가해줘", "client_request_id": str(uuid.uuid4())}
    model = scripted(call("add_todo", title="우유 사기"), text("추가했습니다."))

    with agent.override(model=model.model()):
        first = await client.post(url(conversation), json=body)
        second = await client.post(url(conversation), json=body)

    assert first.status_code == 202
    assert second.status_code == 202
    assert second.json() == first.json()
    assert await count(db_session, Task) == 1
    assert await count(db_session, Todo) == 1
    user_messages = await db_session.scalar(select(func.count()).select_from(Message).where(Message.role == "user"))
    assert user_messages == 1


async def test_same_id_with_different_content_is_409(
    client: httpx.AsyncClient, agent: MyAgent, conversation: int, db_session: AsyncSession
) -> None:
    request_id = str(uuid.uuid4())
    with agent.override(model=scripted(text("네")).model()):
        await client.post(url(conversation), json={"content": "안녕", "client_request_id": request_id})
        response = await client.post(url(conversation), json={"content": "다른 말", "client_request_id": request_id})

    assert response.status_code == 409
    assert await count(db_session, Task) == 1


async def test_same_id_in_other_conversation_is_409(
    client: httpx.AsyncClient, agent: MyAgent, conversation: int, db_session: AsyncSession
) -> None:
    other = (await client.post("/api/conversations", json={})).json()["id"]
    body = {"content": "안녕", "client_request_id": str(uuid.uuid4())}
    with agent.override(model=scripted(text("네")).model()):
        await client.post(url(conversation), json=body)
        response = await client.post(url(other), json=body)

    assert response.status_code == 409
    assert await count(db_session, Task) == 1


async def test_client_request_id_is_required(client: httpx.AsyncClient, conversation: int) -> None:
    response = await client.post(url(conversation), json={"content": "안녕"})

    assert response.status_code == 422


async def test_same_id_from_other_user_is_independent(
    client: httpx.AsyncClient, agent: MyAgent, conversation: int, make_user: UserFactory, db_session: AsyncSession
) -> None:
    """고유성은 사용자 범위다. 다른 사용자가 같은 값을 써도 서로 영향이 없다."""
    request_id = str(uuid.uuid4())
    with agent.override(model=scripted(text("네"), text("네")).model()):
        mine = await client.post(url(conversation), json={"content": "안녕", "client_request_id": request_id})
        await make_user("other@example.com", "password-other")
        await login(client, "other@example.com", "password-other")
        other_conversation = (await client.post("/api/conversations", json={})).json()["id"]
        theirs = await client.post(url(other_conversation), json={"content": "안녕", "client_request_id": request_id})

    assert mine.status_code == 202
    assert theirs.status_code == 202
    assert theirs.json()["task_id"] != mine.json()["task_id"]
    assert await db_session.scalar(select(func.count()).select_from(Conversation)) == 2
