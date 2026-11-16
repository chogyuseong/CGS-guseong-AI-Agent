"""FastAPI 앱을 만든다.

실행: uv run python -m uvicorn app.main:create_app --factory --loop asyncio:SelectorEventLoop
- --factory: 모듈을 불러올 때가 아니라 서버가 시작될 때 create_app()을 부른다.
  그래서 테스트는 .env 없이 테스트용 설정으로 create_app(settings)를 부를 수 있다.
- --loop: Windows에서 psycopg 비동기가 동작하도록 SelectorEventLoop를 쓴다(app/event_loop.py).
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pydantic_ai
from fastapi import FastAPI

from app.agent.agent import build_agent
from app.agent.guard import build_guard_agent
from app.config import Settings, get_settings
from app.db import create_engine_for, create_session_factory
from app.rate_limit import SlidingWindowRateLimiter
from app.routers import api_router
from app.security import add_security_middleware


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    # Pydantic AI 2.68은 첫 실행 때 Logfire 안내 배너를 터미널에 찍는다. 서버 로그를 어지럽히므로 끈다.
    # (PYDANTIC_AI_NO_BANNER 환경변수와 같은 효과. 설치된 버전의 pydantic_ai.BANNER_ENABLED 문서 참고)
    pydantic_ai.BANNER_ENABLED = False
    engine = create_engine_for(settings.database_url)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        yield
        # 서버가 꺼질 때 DB 연결을 정리한다.
        await engine.dispose()

    app = FastAPI(
        title="마이에이전트 API",
        version="0.1.0",
        lifespan=lifespan,
        # 문서도 /api 아래에 둔다. 웹의 rewrites(/api/*)로 같은 주소에서 볼 수 있다.
        openapi_url="/api/openapi.json",
        docs_url="/api/docs",
        redoc_url=None,
    )
    app.state.settings = settings
    app.state.session_factory = create_session_factory(engine)
    # 에이전트는 앱마다 한 번 만든다(3단계 DBOS는 워크플로 시작 전에 Agent가 만들어져 있기를 요구한다).
    app.state.agent = build_agent(settings)
    # 감시 에이전트(도구 없음, AGENT_MODEL_GUARD). EXTERNAL 승인 요청마다 한 번 검사한다.
    app.state.guard_agent = build_guard_agent(settings)
    app.state.message_rate_limiter = SlidingWindowRateLimiter()
    add_security_middleware(app, settings)
    app.include_router(api_router)
    return app
