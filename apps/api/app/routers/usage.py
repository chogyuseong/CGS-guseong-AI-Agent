"""비용 API (9단계 2번, 비용 화면용).

    GET /api/usage/daily?days=14  → 200 UsageOut
        days: 1~90(기본 14). 오늘을 포함한 최근 N일(서울 날짜).
        일별 토큰·비용, 용도별(agent, guard, watch, eval) 합계, 모델별 합계, 기간 합계, 오늘 비용과 남은 한도.

- 경로는 BUILD_SPEC 9절의 `GET /usage/daily`다(9단계 보강에서 옛 `GET /api/usage`를 없앴다).
- 로그인 필수, 내 usage_logs만 합친다.
- 날짜 경계는 하루 비용 상한 검사와 같은 Asia/Seoul 0시다(app/usage_report.py).
"""

from typing import Annotated

from fastapi import APIRouter, Query, Request

from app.config import Settings
from app.db import DbSession
from app.schemas.usage import UsageOut
from app.security import CurrentUser
from app.usage_report import DEFAULT_DAYS, MAX_DAYS, usage_summary

router = APIRouter(prefix="/usage", tags=["usage"])


@router.get("/daily")
async def get_usage(
    request: Request,
    user: CurrentUser,
    session: DbSession,
    days: Annotated[int, Query(ge=1, le=MAX_DAYS, description="오늘을 포함한 최근 며칠(서울 날짜)")] = DEFAULT_DAYS,
) -> UsageOut:
    settings: Settings = request.app.state.settings
    return await usage_summary(session, user.id, settings, days=days)
