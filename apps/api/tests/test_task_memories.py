"""작업 상세의 '함께 보낸 기억'(2026-11-19, 분석 38번).

모델이 어떤 기억을 실제로 썼는지는 서버가 알 수 없다. 대신 그 작업을 시작할 때 모델 지시문에 들어가는 기억
(그때 있던 것 중 최근 30개, prompts.memory_instruction과 같은 기준)을 작업 상세에 보여 준다. 새로 저장하지 않고
읽을 때 계산하므로 지운 기억은 빠진다. 가짜 모델만 쓴다.
"""

from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.agent import MyAgent
from app.agent.prompts import PROMPT_MEMORY_LINES
from app.models import Memory, User
from tests.conftest import UserFactory
from tests.model_scripts import scripted, text
from tests.test_agent_run import send


async def add_memory(db: AsyncSession, user: User, content: str, created_at: datetime) -> Memory:
    memory = Memory(user_id=user.id, content=content, created_at=created_at)
    db.add(memory)
    await db.commit()
    return memory


async def chat(client: httpx.AsyncClient, agent: MyAgent, conversation: int) -> dict:
    with agent.override(model=scripted(text("네")).model()):
        return await send(client, conversation, "저녁 메뉴 추천해 줘")


async def detail(client: httpx.AsyncClient, task_id: int) -> dict:
    response = await client.get(f"/api/tasks/{task_id}")
    assert response.status_code == 200
    return response.json()


async def test_memories_present_at_start_are_listed_in_prompt_order(
    client: httpx.AsyncClient,
    agent: MyAgent,
    conversation: int,
    db_session: AsyncSession,
    logged_in_user: User,
    make_user: UserFactory,
) -> None:
    now = datetime.now(UTC)
    await add_memory(db_session, logged_in_user, "매운 음식을 못 먹는다", now - timedelta(hours=2))
    await add_memory(db_session, logged_in_user, "채식을 한다", now - timedelta(hours=1))
    other = await make_user("other-memory@example.com", "other-password-123")
    await add_memory(db_session, other, "남의 기억", now - timedelta(hours=1))

    task = await chat(client, agent, conversation)
    await add_memory(db_session, logged_in_user, "작업 뒤에 생긴 기억", datetime.now(UTC) + timedelta(hours=1))

    sent = (await detail(client, task["id"]))["memories_sent"]
    assert [m["content"] for m in sent] == ["매운 음식을 못 먹는다", "채식을 한다"]  # 지시문과 같은 순서(오래된 것부터)


async def test_deleted_memory_disappears(
    client: httpx.AsyncClient, agent: MyAgent, conversation: int, db_session: AsyncSession, logged_in_user: User
) -> None:
    memory = await add_memory(db_session, logged_in_user, "지울 기억", datetime.now(UTC) - timedelta(hours=1))
    task = await chat(client, agent, conversation)

    await db_session.delete(memory)
    await db_session.commit()

    assert (await detail(client, task["id"]))["memories_sent"] == []


async def test_only_the_recent_limit_is_listed(
    client: httpx.AsyncClient, agent: MyAgent, conversation: int, db_session: AsyncSession, logged_in_user: User
) -> None:
    start = datetime.now(UTC) - timedelta(days=1)
    for number in range(PROMPT_MEMORY_LINES + 5):
        await add_memory(db_session, logged_in_user, f"기억 {number}", start + timedelta(minutes=number))

    task = await chat(client, agent, conversation)

    sent = (await detail(client, task["id"]))["memories_sent"]
    assert len(sent) == PROMPT_MEMORY_LINES
    assert sent[0]["content"] == "기억 5" and sent[-1]["content"] == f"기억 {PROMPT_MEMORY_LINES + 4}"
