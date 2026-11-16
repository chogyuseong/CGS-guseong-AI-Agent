"""FastAPI 앱을 만든다.

실행: uv run python -m uvicorn app.main:create_app --factory --loop asyncio:SelectorEventLoop
- --factory: 모듈을 불러올 때가 아니라 서버가 시작될 때 create_app()을 부른다.
  그래서 테스트는 .env 없이 테스트용 설정으로 create_app(settings)를 부를 수 있다.
- --loop: Windows에서 psycopg 비동기가 동작하도록 SelectorEventLoop를 쓴다(app/event_loop.py).
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import Settings, get_settings
from app.db import create_engine_for, create_session_factory
from app.routers import api_router
from app.security import add_security_middleware


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
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
    add_security_middleware(app, settings)
    app.include_router(api_router)
    return app
