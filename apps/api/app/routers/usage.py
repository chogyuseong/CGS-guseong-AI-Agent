"""비용 API (9단계 2번, 비용 화면용).

    GET /api/usage/daily?days=14  → 200 UsageOut
        days: 1~90(기본 14). 오늘을 포함한 최근 N일(서울 날짜).
        일별 토큰·비용, 용도별(agent, guard, watch, eval) 합계, 모델별 합계, 기간 합계, 오늘 비용과 남은 한도.

- 경로는 BUILD_SPEC 9절의 `GET /usage/daily`다(9단계 보강에서 옛 `GET /api/usage`를 없앴다).
- 로그인 필수, 내 usage_logs만 합친다.
- 날짜 경계는 하루 비용 상한 검사와 같은 Asia/Seoul 0시다(app/usage_report.py).
- 읽기만 한다. 합계 계산은 모두 usage_summary(app/usage_report.py)에 있고 이 파일은 경로·인자 검사만 맡는다.
- 근거: DECISIONS 2026-11-18(옛 GET /api/usage를 없애고 명세 이름으로 옮김, 별칭 없음).

고칠 때 주의:
- 경로·응답 모양·days 범위를 바꾸면 gen:api 후 apps/web/lib/usage.ts를 고친다. days 범위(DEFAULT_DAYS, MAX_DAYS)는
  app/usage_report.py에 있다.
- 관련 테스트: tests/test_usage_api.py, test_observability.py(트레이스 경로 이름).
"""

from typing import Annotated

from fastapi import APIRouter, Query, Request

from app.config import Settings
from app.db import DbSession
from app.schemas.usage import UsageOut
from app.security import CurrentUser
from app.usage_report import DEFAULT_DAYS, MAX_DAYS, usage_summary

router = APIRouter(prefix="/usage", tags=["usage"])


# 내 비용 합계. days가 범위 밖이면 FastAPI가 422. 소유자 조건(user.id)은 usage_summary 안에서 건다.
@router.get("/daily")
async def get_usage(
    request: Request,
    user: CurrentUser,
    session: DbSession,
    days: Annotated[int, Query(ge=1, le=MAX_DAYS, description="오늘을 포함한 최근 며칠(서울 날짜)")] = DEFAULT_DAYS,
) -> UsageOut:
    settings: Settings = request.app.state.settings
    return await usage_summary(session, user.id, settings, days=days)
