"""영화 비서 C 둘째 단계: 추천 카드 도구 recommend_movies(app/tools/boxoffice.py) 테스트.

기준은 이번 작업에서 마지막으로 성공한 list_now_showing 단계 기록이다(설계 "빌더 점검 뒤 정한 것").
외부 요청은 없다(fake_http에 아무것도 등록하지 않아 요청이 가면 테스트가 깨진다).
"""

from dataclasses import replace

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.deps import AgentDeps
from app.agent.results import ToolResult
from app.models import Step
from app.models.enums import RiskLevel, StepStatus
from app.tools import boxoffice
from tests.conftest import tool_context

SNOW = {
    "code": "20247221",
    "제목": "폭설",
    "개봉일": "2026-11-18",
    "순위": 1,
    "누적 관객": "63,697명",
    "장르": "미스터리, 드라마",
    "상영 시간": "88분",
    "등급": "15세이상관람가",
    "감독": "박선우, 홍의정",
    "배우": "김윤석, 구교환",
    "줄거리": "퇴직을 하루 앞둔 역장…",
    "평점": "6.0",
    "평가 수": 1,
}
HUNGER = {**SNOW, "code": "20267121", "제목": "헝거게임 50번째 추첨의 날", "장르": "액션, SF", "등급": "15세이상관람가"}
CONCERT = {**SNOW, "code": "20268264", "제목": "공연 실황", "장르": "공연"}


def lookup_output(movies: list[dict], tmdb: bool = True, day: str = "2026-11-18") -> dict:
    """ToolResult.external(...).model_dump(mode="json") 모양 그대로(실제 단계 기록과 같게)."""
    content = {"기준일": day, "영화": movies, "출처": {"KOBIS": True, "TMDB": tmdb}}
    return ToolResult.external(content, ["https://www.kobis.or.kr"]).model_dump(mode="json")


async def add_step(db: AsyncSession, deps: AgentDeps, order: int, output: dict | None, status=StepStatus.DONE) -> None:
    db.add(
        Step(
            task_id=deps.task_id,
            order=order,
            tool_name="list_now_showing",
            tool_call_id=f"c-{order}",
            risk_level=RiskLevel.READ,
            input={},
            output=output,
            status=status,
            label="상영작 확인하는 중",
        )
    )
    await db.commit()


async def recommend(deps: AgentDeps, picks: list[dict], taste: str | None = None) -> ToolResult:
    ctx = tool_context(deps, "recommend_movies")
    return await boxoffice.recommend_movies(ctx, [boxoffice.Pick(**p) for p in picks], taste)


def card(result: ToolResult) -> dict:
    assert result.ok and isinstance(result.data, dict)
    return result.data


async def test_requires_successful_lookup(tool_deps, db_session):
    result = await recommend(tool_deps, [{"code": "20247221", "reason": "긴장감"}])
    assert not result.ok
    assert "먼저 상영작을 조회" in (result.user_message or "")
    await add_step(db_session, tool_deps, 1, {"status": "failed", "data": None}, StepStatus.FAILED)
    result = await recommend(tool_deps, [{"code": "20247221", "reason": "긴장감"}])
    assert not result.ok
    assert "먼저 상영작을 조회" in (result.user_message or "")


async def test_unknown_code_rejected(tool_deps, db_session):
    await add_step(db_session, tool_deps, 1, lookup_output([SNOW, HUNGER]))
    result = await recommend(tool_deps, [{"code": "20247221", "reason": "좋음"}, {"code": "99999999", "reason": "?"}])
    assert not result.ok
    assert "99999999" in (result.user_message or "")
    assert result.error_code == "invalid"


async def test_duplicate_code_rejected(tool_deps, db_session):
    await add_step(db_session, tool_deps, 1, lookup_output([SNOW, HUNGER]))
    result = await recommend(tool_deps, [{"code": "20247221", "reason": "a"}, {"code": "20247221", "reason": "b"}])
    assert not result.ok
    assert result.error_code == "invalid"


async def test_facts_come_from_lookup(tool_deps, db_session):
    await add_step(db_session, tool_deps, 1, lookup_output([SNOW, HUNGER, CONCERT]))
    result = await recommend(
        tool_deps,
        [{"code": "20267121", "reason": "시리즈 팬이면 좋아요"}, {"code": "20247221", "reason": "조용한 긴장감"}],
        taste="친구랑 볼 만한 것",
    )
    data = card(result)
    assert data["결론"] == "헝거게임 50번째 추첨의 날 추천"
    assert data["기준일"] == "2026-11-18"
    assert data["조건"] == "친구랑 볼 만한 것"
    first, second = data["추천"]
    assert first["제목"] == "헝거게임 50번째 추첨의 날"
    assert first["이유"] == "시리즈 팬이면 좋아요"
    assert first["장르"] == "액션, SF"
    assert second["상영 시간"] == "88분"
    assert second["평점"] == "6.0"
    assert second["평가 수"] == 1  # 2026-11-19: 표본이 작은 평점을 알아볼 수 있게 평가 수도 함께
    assert "줄거리" not in second  # 카드에는 줄거리를 넣지 않는다(외부 글을 새로 내보내지 않음)
    assert "시간표를 붙여 주시면" in data["다음"]
    assert data["예매"] == list(boxoffice.BOOKING_LINKS)
    assert data["출처 표기"] == boxoffice.TMDB_NOTICE


async def test_no_tmdb_no_notice(tool_deps, db_session):
    plain = {k: v for k, v in SNOW.items() if k not in ("줄거리", "평점", "평가 수")}
    await add_step(db_session, tool_deps, 1, lookup_output([plain], tmdb=False))
    data = card(await recommend(tool_deps, [{"code": "20247221", "reason": "a"}]))
    assert "출처 표기" not in data
    assert data["추천"][0]["평점"] == "정보 없음"
    assert data["추천"][0]["평가 수"] == "정보 없음"


async def test_uses_latest_lookup(tool_deps, db_session):
    await add_step(db_session, tool_deps, 1, lookup_output([SNOW], day="2026-11-17"))
    await add_step(db_session, tool_deps, 2, lookup_output([HUNGER], day="2026-11-18"))
    assert not (await recommend(tool_deps, [{"code": "20247221", "reason": "a"}])).ok
    data = card(await recommend(tool_deps, [{"code": "20267121", "reason": "a"}]))
    assert data["기준일"] == "2026-11-18"


async def test_other_users_step_ignored(tool_deps, db_session):
    await add_step(db_session, tool_deps, 1, lookup_output([SNOW]))
    stranger = replace(tool_deps, user_id=tool_deps.user_id + 999)
    result = await recommend(stranger, [{"code": "20247221", "reason": "a"}])
    assert not result.ok


def test_label() -> None:
    assert boxoffice.recommend_movies_label({"picks": [{}, {}]}) == "영화 추천 정리하는 중"
