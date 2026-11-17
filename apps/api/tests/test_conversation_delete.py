"""5단계 보강(2): 대화 삭제 DELETE /api/conversations/{id} (BUILD_SPEC 5.5, 9.3).

- 내 대화만(남의 대화는 404, 정보 노출 없음). 상태를 바꾸므로 Origin 검사.
- 진행 중인 작업(대기·실행·승인 대기·취소 요청·재조회)이 있으면 409
  (실행 중인 워크플로 기록을 지워 복구를 망가뜨리지 않음).
- 지우는 것: 메시지, 작업, 단계, 승인·실행 계획·operation·영수증·outbox, 그 작업들의 DBOS 워크플로 기록
  (워크플로 입력과 단계 결과에 사용자 메시지·모델 기록이 있다).
- 남기는 것: 이 대화에서 만든 기억(사용자가 명시적으로 남긴 것. 출처 링크만 끊고 근거 인용은 유지),
  비용 기록(usage_logs, 내용 없음. 작업 연결만 끊김).
"""

import httpx
from dbos import DBOS
from sqlalchemy import ColumnElement, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.agent import MyAgent
from app.models import AgentRun, Memory, Message, Step, Task, UsageLog, User
from tests.conftest import login
from tests.fake_tools import FakeExternalCounter
from tests.model_scripts import call, scripted, text
from tests.test_agent_run import send
from tests.test_approvals import external_then_answer


async def count(db_session: AsyncSession, model: type, *where: ColumnElement[bool]) -> int:
    return await db_session.scalar(select(func.count()).select_from(model).where(*where)) or 0


async def test_delete_conversation_removes_records_but_keeps_memory_and_cost(
    client: httpx.AsyncClient, agent: MyAgent, conversation: int, db_session: AsyncSession, logged_in_user: User
) -> None:
    user_id = logged_in_user.id
    model = scripted(call("remember", content="매운 거 못 먹음", quote="나 매운 거 못 먹어"), text("기억했습니다."))
    with agent.override(model=model.model()):
        task = await send(client, conversation, "나 매운 거 못 먹어, 기억해")
    runs = await db_session.scalars(select(AgentRun.workflow_id).where(AgentRun.task_id == task["id"]))
    workflow_ids = [workflow_id for workflow_id in runs if workflow_id]
    assert workflow_ids and await DBOS.get_workflow_status_async(workflow_ids[0]) is not None

    response = await client.delete(f"/api/conversations/{conversation}")

    db_session.expire_all()
    assert response.status_code == 204
    assert await count(db_session, Message, Message.conversation_id == conversation) == 0
    assert await count(db_session, Task, Task.id == task["id"]) == 0
    assert await count(db_session, Step, Step.task_id == task["id"]) == 0
    for workflow_id in workflow_ids:
        assert await DBOS.get_workflow_status_async(workflow_id) is None
    [memory] = (await db_session.scalars(select(Memory).where(Memory.user_id == user_id))).all()
    assert memory.source_message_id is None
    assert memory.source_quote == "나 매운 거 못 먹어"
    assert await count(db_session, UsageLog, UsageLog.user_id == user_id) >= 1
    listed = (await client.get("/api/memories")).json()["memories"]
    assert listed[0]["source"] is None and listed[0]["source_quote"] == "나 매운 거 못 먹어"
    assert (await client.get(f"/api/conversations/{conversation}/messages")).status_code == 404


async def test_conversation_with_task_in_progress_is_not_deleted(
    client: httpx.AsyncClient, agent: MyAgent, conversation: int, fake_external_counter: FakeExternalCounter
) -> None:
    with agent.override(model=external_then_answer().model()):
        task = await send(client, conversation, "외부 도구 실행해줘")
    assert task["status"] == "waiting_approval"

    response = await client.delete(f"/api/conversations/{conversation}")

    assert response.status_code == 409
    assert "진행 중" in response.json()["detail"]
    assert (await client.get(f"/api/conversations/{conversation}/messages")).status_code == 200


async def test_other_users_conversation_is_not_found(
    client: httpx.AsyncClient, conversation: int, make_user, db_session: AsyncSession
) -> None:
    await make_user("delete-other@example.com", "password-other")
    await db_session.commit()
    await client.post("/api/auth/logout")
    assert (await login(client, "delete-other@example.com", "password-other")).status_code == 200

    response = await client.delete(f"/api/conversations/{conversation}")

    assert response.status_code == 404


async def test_delete_requires_origin(client: httpx.AsyncClient, conversation: int) -> None:
    response = await client.delete(f"/api/conversations/{conversation}", headers={"Origin": "http://evil.example"})

    assert response.status_code == 403
