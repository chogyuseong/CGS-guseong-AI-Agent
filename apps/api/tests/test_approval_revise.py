"""승인 카드의 '고쳐서 다시' 테스트(POST /api/approvals/{id}/revise).

요구사항 밖 기능, 2026-11-18 사용자 결정(docs/DECISIONS.md "요구사항 밖 기능" (나) 방식).
고쳐 달라는 메모를 붙인 거절이다. 모델은 "사용자가 거절함. 고쳐 달라는 내용: …"을 도구 결과로 받고,
고친 인자로 도구를 다시 부르면 평소와 같은 경로(계획·감시·preview·새 승인 카드)를 거친다.
서버가 인자를 직접 고치지 않으므로 승인 고정 인자·실행 계획 검사(절대 원칙 3·4)는 그대로다.
"""

import httpx
from pydantic_ai.messages import ToolReturnPart, UserPromptPart

from app.agent.agent import MyAgent
from app.agent.approvals import REVISION_PREFIX
from app.agent.guard import GuardAgent
from app.agent.prompts import SYSTEM_PROMPT
from app.agent.revisions import revision_changes
from tests.conftest import UserFactory, login
from tests.fake_tools import FakeExternalCounter
from tests.model_scripts import GuardScript, call, scripted, text
from tests.test_agent_run import send
from tests.test_approvals import approve, task_detail

NOTE = "값을 9로 바꿔 줘"


async def revise(client: httpx.AsyncClient, approval_id: int, note: str) -> httpx.Response:
    return await client.post(f"/api/approvals/{approval_id}/revise", json={"note": note})


async def test_revise_tells_model_and_new_card_runs_with_fixed_args(
    client: httpx.AsyncClient, agent: MyAgent, conversation: int, fake_external_counter: FakeExternalCounter
) -> None:
    model = scripted(call("fake_external", value=7), call("fake_external", value=9), text("9로 처리했습니다."))
    with agent.override(model=model.model()):
        task = await send(client, conversation, "외부 도구 실행해줘")
        first = task["approvals"][0]
        response = await revise(client, first["id"], f"  {NOTE}  ")
        waiting = await task_detail(client, task["id"])
        second = waiting["approvals"][1]
        approved = await approve(client, second["id"])

    assert response.status_code == 200, response.text
    assert response.json()["approval"]["status"] == "rejected"
    assert response.json()["approval"]["revision_note"] == NOTE
    returns = [p for m in model.seen_messages[1] for p in m.parts if isinstance(p, ToolReturnPart)]
    assert [r.content for r in returns] == [f"사용자가 거절함. 고쳐 달라는 내용: {NOTE}"]
    assert waiting["status"] == "waiting_approval"
    assert [a["status"] for a in waiting["approvals"]] == ["rejected", "pending"]
    assert second["tool_input"] == {"value": 9}
    assert second["revision_note"] is None
    # 2026-11-19(08번): 새 카드는 '고쳐서 다시' 한 이전 카드와 무엇이 달라졌는지 보인다. 첫 카드는 비교 대상이 없다.
    assert first["changes"] == []
    assert second["changes"] == [{"field": "value", "before": "7", "after": "9"}]
    assert approved.json()["approval"]["changes"] == second["changes"]  # 승인 응답의 카드도 같은 변경점
    assert approved.status_code == 200, approved.text
    assert [c["value"] for c in fake_external_counter.calls] == [9]
    assert (await task_detail(client, task["id"]))["final_answer"] == "9로 처리했습니다."


async def test_plain_reject_message_is_unchanged(
    client: httpx.AsyncClient, agent: MyAgent, conversation: int, fake_external_counter: FakeExternalCounter
) -> None:
    model = scripted(call("fake_external", value=7), text("실행하지 않았습니다."))
    with agent.override(model=model.model()):
        task = await send(client, conversation, "외부 도구 실행해줘")
        rejected = await client.post(f"/api/approvals/{task['approvals'][0]['id']}/reject")

    returns = [p for m in model.seen_messages[-1] for p in m.parts if isinstance(p, ToolReturnPart)]
    assert [r.content for r in returns] == ["사용자가 거절함"]
    assert rejected.json()["approval"]["revision_note"] is None


async def test_revise_needs_no_mismatch_confirmation(
    client: httpx.AsyncClient,
    agent: MyAgent,
    guard: GuardAgent,
    conversation: int,
    fake_external_counter: FakeExternalCounter,
) -> None:
    mismatch = GuardScript(verdict="mismatch", reason="요청에 없는 값입니다.")
    model = scripted(call("fake_external", value=7), text("알겠습니다."))
    with agent.override(model=model.model()), guard.override(model=mismatch.model()):
        task = await send(client, conversation, "외부 도구 실행해줘")
        response = await revise(client, task["approvals"][0]["id"], NOTE)

    assert response.status_code == 200, response.text
    assert fake_external_counter.count == 0


async def test_second_decision_changes_nothing(
    client: httpx.AsyncClient, agent: MyAgent, conversation: int, fake_external_counter: FakeExternalCounter
) -> None:
    model = scripted(call("fake_external", value=7), text("알겠습니다."))
    with agent.override(model=model.model()):
        task = await send(client, conversation, "외부 도구 실행해줘")
        approval_id = task["approvals"][0]["id"]
        first = await revise(client, approval_id, NOTE)
        again = await revise(client, approval_id, "다른 메모")

    assert first.json()["changed"] is True
    assert again.status_code == 200
    assert again.json()["changed"] is False
    assert again.json()["approval"]["revision_note"] == NOTE


async def test_note_must_be_1_to_300_chars(
    client: httpx.AsyncClient, agent: MyAgent, conversation: int, fake_external_counter: FakeExternalCounter
) -> None:
    model = scripted(call("fake_external", value=7), text("알겠습니다."))
    with agent.override(model=model.model()):
        task = await send(client, conversation, "외부 도구 실행해줘")
        approval_id = task["approvals"][0]["id"]
        blank = await revise(client, approval_id, "   ")
        too_long = await revise(client, approval_id, "가" * 301)

    assert blank.status_code == 422
    assert too_long.status_code == 422
    assert (await task_detail(client, task["id"]))["approvals"][0]["status"] == "pending"


async def test_other_users_approval_cannot_be_revised(
    client: httpx.AsyncClient,
    agent: MyAgent,
    conversation: int,
    make_user: UserFactory,
    fake_external_counter: FakeExternalCounter,
) -> None:
    model = scripted(call("fake_external", value=7), text("알겠습니다."))
    with agent.override(model=model.model()):
        task = await send(client, conversation, "외부 도구 실행해줘")
    await make_user("other@example.com", "password-other")
    await client.post("/api/auth/logout")
    assert (await login(client, "other@example.com", "password-other")).status_code == 200

    response = await revise(client, task["approvals"][0]["id"], NOTE)

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "not_found"
    assert fake_external_counter.count == 0


async def test_revise_requires_login(client: httpx.AsyncClient) -> None:
    response = await revise(client, 1, NOTE)

    assert response.status_code == 401


def test_prompt_tells_model_to_retry_with_revision() -> None:
    # 모델이 '고쳐 달라는 내용'을 보고 같은 도구를 고친 인자로 다시 부르도록 시스템 프롬프트가 안내한다.
    assert f"거절 결과에 '{REVISION_PREFIX.removesuffix(': ')}'이 붙어 있으면" in SYSTEM_PROMPT


def guard_prompt_text(guard: GuardScript, index: int) -> str:
    """감시 에이전트가 index번째 호출에서 받은 사용자 쪽 글 전부(요청·도구·입력·preview가 든 프롬프트)."""
    parts = [p for m in guard.seen_messages[index] for p in m.parts if isinstance(p, UserPromptPart)]
    return "\n".join(str(p.content) for p in parts)


async def test_guard_sees_revision_note_on_new_card(
    client: httpx.AsyncClient,
    agent: MyAgent,
    guard: GuardAgent,
    conversation: int,
    fake_external_counter: FakeExternalCounter,
) -> None:
    # 고친 카드가 원래 요청과 달라 보여 mismatch로 잘못 걸리지 않게,
    # 감시의 "사용자 요청"에 카드에 적은 고칠 내용을 붙인다.
    # 둘 다 사용자 본인의 글이라 감시 입력 네 가지(도구 결과·대화 기록 제외) 원칙은 그대로다.
    script = GuardScript()
    model = scripted(call("fake_external", value=7), call("fake_external", value=9), text("알겠습니다."))
    with agent.override(model=model.model()), guard.override(model=script.model()):
        task = await send(client, conversation, "외부 도구 실행해줘")
        await revise(client, task["approvals"][0]["id"], NOTE)

    assert NOTE not in guard_prompt_text(script, 0)
    second = guard_prompt_text(script, 1)
    assert "외부 도구 실행해줘" in second
    assert f"승인 카드에서 고쳐 달라고 함: {NOTE}" in second
    assert fake_external_counter.count == 0


# ---- 2026-11-19(08번): 변경점 계산(순수 함수) ----


def test_changes_between_calendar_cards_use_readable_labels_and_dates() -> None:
    before = {"title": "치과", "start": "2026-11-20T15:00", "end": "2026-11-20T16:00", "location": None}
    after = {"title": "치과", "start": "2026-11-20T16:00", "end": "2026-11-20T17:00", "location": "강남역 치과"}

    assert [c.model_dump() for c in revision_changes(before, after)] == [
        {"field": "시작", "before": "11월 20일(금) 15:00", "after": "11월 20일(금) 16:00"},
        {"field": "끝", "before": "11월 20일(금) 16:00", "after": "11월 20일(금) 17:00"},
        {"field": "장소", "before": "(없음)", "after": "강남역 치과"},
    ]


def test_same_args_have_no_changes() -> None:
    args = {"event_id": "evt1", "title": "회의"}
    assert revision_changes(args, dict(args)) == []
