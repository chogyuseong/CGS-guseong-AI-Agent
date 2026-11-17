"""도구 함수를 직접 부르는 테스트가 "승인 API를 거친 것과 같은 DB 상태"를 만드는 도우미(2단계 보강).

실제 경로와 같은 함수를 쓴다: plans.draft_plan(정규화·해시) → approvals.save_requests(계획·operation·승인)
→ approvals.decide(승인, 실행 유효 시간). 재개 워크플로는 시작하지 않는다.
"""

from collections.abc import Sequence
from typing import Any

from pydantic_ai.toolsets import FunctionToolset
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent import approvals, plans
from app.agent.deps import AgentDeps
from app.agent.toolsets import external_tools
from app.models import Approval, Step, Task
from app.models.enums import TaskOrigin, TaskStatus, VerdictKind


async def approve_directly(
    session: AsyncSession,
    deps: AgentDeps,
    tool_name: str,
    call_id: str,
    args: dict[str, Any],
    toolsets: Sequence[FunctionToolset[Any]] = (external_tools,),
) -> int:
    """승인된 실행 계획을 만들고 승인 id를 돌려준다. task는 다시 running(재개 워크플로가 잡은 상태)으로 둔다."""
    draft = await plans.draft_plan(session, user_id=deps.user_id, toolsets=toolsets, tool_name=tool_name, args=args)
    draft = await plans.with_target_version(deps, draft)
    request = approvals.ApprovalRequest(
        tool_call_id=call_id,
        plan=draft,
        label="테스트",
        summary="테스트",
        verdict=VerdictKind.MATCH,
        reason="테스트",
        guard_model="test",
    )
    await approvals.save_requests(
        session, task_id=deps.task_id, user_id=deps.user_id, origin=TaskOrigin.CHAT, requests=[request]
    )
    await session.execute(update(Task).where(Task.id == deps.task_id).values(status=TaskStatus.WAITING_APPROVAL))
    await session.commit()
    approval_id = await session.scalar(
        select(Approval.id)
        .join(Step, Step.id == Approval.step_id)
        .where(Step.task_id == deps.task_id, Approval.tool_call_id == call_id)
    )
    assert approval_id is not None
    await approvals.decide(session, approval_id, deps.user_id, approve=True)
    await session.execute(update(Task).where(Task.id == deps.task_id).values(status=TaskStatus.RUNNING))
    await session.commit()
    return approval_id
