"""6단계 보강(4): 구조화된 제안(BUILD_SPEC 8.3).

예약 작업은 [제안] 문자열 대신 AutomationReply(text, suggestions[title, proposed_user_message, reason])로 답한다.
서버가 messages.suggestions에 저장하고 화면은 누르면 입력창에 문장만 채운다(사용자가 보낸다).
제안은 승인·권한을 전달하지 않는다.
일반 텍스트로 답해도 실행은 끝난다(제안 없음). 채팅 task의 출력 형식은 바꾸지 않는다.
"""

from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.agent import MyAgent
from app.agent.replies import AutomationReply
from app.automation import rules
from app.automation.check import AutomationServices, run_check
from app.models import Conversation, Message, User
from tests.model_scripts import call, scripted, text
from tests.test_automation_runs import add_job

SUGGESTION = {
    "title": "우산 챙기기",
    "proposed_user_message": "내일 아침 '우산 챙기기' 할 일 추가해줘",
    "reason": "내일 강수 확률이 70%입니다.",
}


async def room_messages(db_session: AsyncSession, user: User) -> list[Message]:
    rows = await db_session.scalars(
        select(Message)
        .join(Conversation)
        .where(Conversation.user_id == user.id, Conversation.title == rules.AUTOMATION_CONVERSATION_TITLE)
        .order_by(Message.id)
    )
    return list(rows)


async def test_schedule_reply_saves_structured_suggestions(
    automation_services: AutomationServices,
    agent: MyAgent,
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    logged_in_user: User,
) -> None:
    now = datetime.now(UTC)
    await add_job(db_session, logged_in_user, now - timedelta(minutes=1))
    reply = {"text": "내일은 비가 옵니다.", "suggestions": [SUGGESTION]}
    model = scripted(call("final_result", **reply))

    with agent.override(model=model.model()):
        await run_check(automation_services, now)

    [message] = await room_messages(db_session, logged_in_user)
    assert message.content == "내일은 비가 옵니다."
    assert message.suggestions == [SUGGESTION]
    listed = (await client.get(f"/api/conversations/{message.conversation_id}/messages")).json()
    assert listed[0]["suggestions"] == [SUGGESTION]


async def test_plain_text_schedule_reply_has_no_suggestions(
    automation_services: AutomationServices, agent: MyAgent, db_session: AsyncSession, logged_in_user: User
) -> None:
    now = datetime.now(UTC)
    await add_job(db_session, logged_in_user, now - timedelta(minutes=1))

    with agent.override(model=scripted(text("오늘 일정은 없습니다.")).model()):
        await run_check(automation_services, now)

    [message] = await room_messages(db_session, logged_in_user)
    assert message.content == "오늘 일정은 없습니다." and message.suggestions is None


def test_suggestions_are_limited() -> None:
    reply = AutomationReply.model_validate({"text": "x", "suggestions": [SUGGESTION] * 3})
    assert len(reply.suggestions) == 3
    too_many = {"text": "x", "suggestions": [SUGGESTION] * 6}
    try:
        AutomationReply.model_validate(too_many)
    except ValueError:
        return
    raise AssertionError("제안은 5개까지")


def test_briefing_prompt_has_no_bracket_suggestions() -> None:
    assert "[제안]" not in rules.BRIEFING_PROMPT
