"""생활 비서 D 첫 단계: 붙여 넣은 채용 공고 + 저장한 내 경험 → 회사별 준비 카드(list_experiences, show_job_prep).

2026-11-18 사용자 결정: 범위 (가) 공고 붙여넣기, 경험 원문은 암호화 + 등록 전 안내(설정 화면 '내 경험').
- 지어낸 요구 사항 막기: 요구 사항 인용이 최근 사용자 메시지(붙여 넣은 공고)에 실제로 있어야 한다.
- 지어낸 경험 막기: 대조의 경험 id가 내 것이고, 근거 인용이 그 경험 본문에 실제로 있어야 한다.
- 저장: list_experiences 결과는 steps.output에 개수만, tasks.message_history에는 암호문만.
- 경험을 읽은 문맥에서는 공개 검색·장소·날씨와 예약·감시 만들기 도구를 주지 않는다(메일과 같은 규칙).
실제 모델은 부르지 않는다(FunctionModel 대본).
"""

import json
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent import data_policy
from app.agent.agent import MyAgent
from app.agent.deps import AgentDeps
from app.models import Task
from app.tools import experiences
from tests.conftest import UserFactory
from tests.fake_http import FakeHttp
from tests.model_scripts import call, scripted, text
from tests.test_agent_run import send
from tests.test_data_policy import add_done_step

SECRET = "EXPSECRET-3307"
BODY = (
    f"{SECRET} 마이에이전트 프로젝트에서 FastAPI와 PostgreSQL로 승인 흐름을 설계했다. "
    "실행 영수증으로 중복 실행을 막았다."
)
POSTING = (
    "이 공고 준비해 줘.\n"
    "[SK 채용] 백엔드 개발자\n"
    "자격 요건: Python 기반 웹 서비스 개발 경험\n"
    "우대 사항: 대용량 트래픽 처리 경험, Kubernetes 운영 경험"
)
PUBLIC_TOOLS = {"web_search", "search_places", "get_weather"}


async def save(deps: AgentDeps, body: str = BODY, title: str = "마이에이전트") -> int:
    cipher = experiences.cipher_for(deps.settings)
    assert cipher is not None
    async with deps.db() as session:
        return (await experiences.save_experience(session, cipher, deps.user_id, title, body)).id


def prep(experience_id: int, **changes: Any):
    args: dict[str, Any] = {
        "company": "SK",
        "role": "백엔드 개발자",
        "requirements": [
            {"quote": "Python 기반 웹 서비스 개발 경험", "kind": "필수"},
            {"quote": "Kubernetes 운영 경험", "kind": "우대"},
        ],
        "matches": [
            {
                "requirement": 1,
                "experience_id": experience_id,
                "evidence": "FastAPI와 PostgreSQL로 승인 흐름을 설계했다",
                "note": "Python 웹 서비스 설계 경험",
            }
        ],
        "gaps": ["Kubernetes 운영 경험이 없음 — 작은 배포 실습 권장"],
        "questions": ["승인 흐름에서 중복 실행을 어떻게 막았나요?"],
        "essay_points": ["실행 영수증 설계를 문제 해결 사례로"],
        "checklist": ["Kubernetes 기초 실습", "프로젝트 아키텍처 1장 정리"],
    }
    return call("show_job_prep", **{**args, **changes})


async def run(client: httpx.AsyncClient, agent: MyAgent, conversation: int, *calls: Any, message: str = POSTING):
    model = scripted(*calls, text("준비 카드를 만들었습니다."))
    with agent.override(model=model.model()):
        return await send(client, conversation, message), model


async def stored_history(db_session: AsyncSession, task_id: int) -> Any:
    return await db_session.scalar(
        select(Task.message_history).where(Task.id == task_id).execution_options(populate_existing=True)
    )


async def test_prep_card_from_posting_and_experience(
    client: httpx.AsyncClient,
    agent: MyAgent,
    conversation: int,
    tool_deps: AgentDeps,
    fake_http: FakeHttp,
    db_session: AsyncSession,
) -> None:
    exp_id = await save(tool_deps)

    task, _ = await run(client, agent, conversation, call("list_experiences"), prep(exp_id))

    listed, card = task["steps"]
    assert listed["ok"] and listed["risk_level"] == "READ"
    assert SECRET not in json.dumps(listed["output"], ensure_ascii=False)  # steps.output에는 개수만
    assert listed["output"]["data"]["content"] == {"stored": False, "experience_count": 1}
    assert card["ok"]
    content = card["output"]["data"]
    assert content["회사"] == "SK" and content["직무"] == "백엔드 개발자"
    first, second = content["대조"]
    assert first["요구"] == "Python 기반 웹 서비스 개발 경험" and first["구분"] == "필수"
    assert first["근거"] == [
        {
            "경험": "마이에이전트",
            "인용": "FastAPI와 PostgreSQL로 승인 흐름을 설계했다",
            "설명": "Python 웹 서비스 설계 경험",
        }
    ]
    assert second["근거"] == []
    assert content["체크리스트"] == ["Kubernetes 기초 실습", "프로젝트 아키텍처 1장 정리"]
    history = await stored_history(db_session, task["id"])
    assert SECRET not in json.dumps(history, ensure_ascii=False)  # 모델 기록에는 암호문만
    assert fake_http.requests == []


async def test_requirement_not_in_posting_is_refused(
    client: httpx.AsyncClient, agent: MyAgent, conversation: int, tool_deps: AgentDeps
) -> None:
    exp_id = await save(tool_deps)
    invented = [{"quote": "Go 언어 3년 이상", "kind": "필수"}]

    task, _ = await run(client, agent, conversation, prep(exp_id, requirements=invented, matches=[]))

    [step] = task["steps"]
    assert not step["ok"]
    assert "요구 사항 1" in step["output"]["user_message"]


async def test_evidence_not_in_experience_is_refused(
    client: httpx.AsyncClient, agent: MyAgent, conversation: int, tool_deps: AgentDeps
) -> None:
    exp_id = await save(tool_deps)
    match = {"requirement": 1, "experience_id": exp_id, "evidence": "트래픽 10만 건을 처리했다", "note": "x"}

    task, _ = await run(client, agent, conversation, prep(exp_id, matches=[match]))

    [step] = task["steps"]
    assert not step["ok"]
    assert "근거 1" in step["output"]["user_message"]


async def test_other_users_experience_cannot_be_cited(
    client: httpx.AsyncClient,
    agent: MyAgent,
    conversation: int,
    tool_deps: AgentDeps,
    make_user: UserFactory,
) -> None:
    other = await make_user("other@example.com", "password-other")
    cipher = experiences.cipher_for(tool_deps.settings)
    assert cipher is not None
    async with tool_deps.db() as session:
        theirs = (await experiences.save_experience(session, cipher, other.id, "남의 것", BODY)).id

    task, _ = await run(client, agent, conversation, prep(theirs))

    [step] = task["steps"]
    assert not step["ok"]
    assert "근거 1" in step["output"]["user_message"]


async def test_match_pointing_to_missing_requirement_is_refused(
    client: httpx.AsyncClient, agent: MyAgent, conversation: int, tool_deps: AgentDeps
) -> None:
    exp_id = await save(tool_deps)
    match = {"requirement": 9, "experience_id": exp_id, "evidence": "승인 흐름을 설계했다", "note": "x"}

    task, _ = await run(client, agent, conversation, prep(exp_id, matches=[match]))

    assert not task["steps"][0]["ok"]


async def test_no_experiences_tells_where_to_register(
    client: httpx.AsyncClient, agent: MyAgent, conversation: int, tool_deps: AgentDeps
) -> None:
    task, _ = await run(client, agent, conversation, call("list_experiences"))

    [step] = task["steps"]
    assert not step["ok"]
    assert step["output"]["user_message"] == experiences.NO_EXPERIENCE_MESSAGE


async def test_public_and_automation_tools_hidden_after_reading_experiences(
    client: httpx.AsyncClient, agent: MyAgent, conversation: int, tool_deps: AgentDeps
) -> None:
    await save(tool_deps)

    _, model = await run(client, agent, conversation, call("list_experiences"))

    before, after = model.seen_tool_names
    assert set(before) >= PUBLIC_TOOLS | {"create_watch"}
    assert not (PUBLIC_TOOLS | {"create_schedule", "create_watch"}) & set(after)
    assert {"show_job_prep", "add_todo"} <= set(after)


async def test_outbound_and_automation_refused_after_experiences_in_task(
    tool_deps: AgentDeps, db_session: AsyncSession
) -> None:
    await add_done_step(db_session, tool_deps, 1, "list_experiences", {"status": "succeeded", "data": {}})

    search = await data_policy.outbound_refusal(tool_deps, "web_search", {"query": "SK 면접 후기"})
    watch = await data_policy.automation_refusal(tool_deps, [], "create_watch")

    assert search is not None and search.error_code == "data_policy"
    assert watch is not None and watch.error_code == "data_policy"


async def test_without_payload_key_list_experiences_is_hidden(
    client: httpx.AsyncClient, agent: MyAgent, conversation: int, tool_deps: AgentDeps
) -> None:
    tool_deps.settings.payload_encryption_key = type(tool_deps.settings.payload_encryption_key)("")

    _, model = await run(client, agent, conversation)

    assert "list_experiences" not in model.seen_tool_names[0]
