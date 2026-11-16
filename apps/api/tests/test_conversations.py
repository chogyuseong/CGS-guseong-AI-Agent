import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Conversation
from tests.conftest import UserFactory, login


async def test_user_cannot_read_other_users_conversation(
    client: httpx.AsyncClient, db_session: AsyncSession, make_user: UserFactory
) -> None:
    await make_user("a@example.com", "password-a")
    user_b = await make_user("b@example.com", "password-b")
    conversation_b = Conversation(user_id=user_b.id, title="B의 대화")
    db_session.add(conversation_b)
    await db_session.flush()

    await login(client, "a@example.com", "password-a")
    messages = await client.get(f"/api/conversations/{conversation_b.id}/messages")
    listing = await client.get("/api/conversations")

    assert messages.status_code == 404
    assert listing.status_code == 200
    assert listing.json() == []
