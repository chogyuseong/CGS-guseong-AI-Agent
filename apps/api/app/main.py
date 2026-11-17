"""FastAPI 앱을 만든다.

실행: uv run python -m uvicorn app.main:create_app --factory --loop asyncio:SelectorEventLoop
- --factory: 모듈을 불러올 때가 아니라 서버가 시작될 때 create_app()을 부른다.
  그래서 테스트는 .env 없이 테스트용 설정으로 create_app(settings)를 부를 수 있다.
- --loop: Windows에서 psycopg 비동기가 동작하도록 SelectorEventLoop를 쓴다(app/event_loop.py).

시작 순서 (3단계):
    create_app: 에이전트를 만든다(DBOS.launch 전에 있어야 한다) → 워크플로가 쓸 app.state를 알려 준다
    lifespan 시작: DBOS를 만들고 launch → 끝나지 않은 워크플로를 DBOS가 이어서 실행(복구)
                   → 6단계: 1분 검사 예약(automation-check)을 DBOS에 등록(같은 이름이면 덮어씀)
                   → 워크플로를 시작하지 못한 task(queued 등)를 이어서 시작
    lifespan 끝: DBOS 종료 → 외부 API HTTP 클라이언트 닫기 → DB 연결 정리
테스트(httpx ASGITransport)는 lifespan을 돌리지 않는다. DBOS는 tests/conftest.py가 따로 시작한다.
"""

from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager

import pydantic_ai
from fastapi import FastAPI
from pydantic_ai.models import Model
from sqlalchemy.ext.asyncio import AsyncEngine

from app.agent import workflows
from app.agent.agent import build_agent
from app.agent.guard import build_guard_agent
from app.automation.watch import build_watch_agent
from app.config import Settings, get_settings
from app.db import create_engine_for, create_session_factory
from app.observability import configure_observability, install_log_redaction, instrument_app
from app.push import send_with_pywebpush
from app.rate_limit import SlidingWindowRateLimiter
from app.routers import api_router
from app.routers.auth import build_login_rate_limiter
from app.security import add_security_middleware
from app.tools.http import build_http_client


def build_lifespan(settings: Settings, engine: AsyncEngine) -> Callable[[FastAPI], AbstractAsyncContextManager[None]]:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        workflows.launch_dbos(settings)
        await workflows.restart_orphaned_tasks(app.state.launcher, app.state.session_factory)
        yield
        # 서버가 꺼질 때 DBOS·HTTP 클라이언트·DB 연결을 정리한다.
        # 끝나지 않은 워크플로는 다음 시작 때 DBOS가 이어서 실행한다.
        workflows.shutdown_dbos()
        await app.state.http_client.aclose()
        await engine.dispose()

    return lifespan


def create_app(settings: Settings | None = None, *, agent_models: Mapping[str, Model] | None = None) -> FastAPI:
    """agent_models: 워크플로 안에서 끼워 쓸 모델 객체(테스트의 가짜 모델만 넘긴다)."""
    settings = settings or get_settings()
    # Pydantic AI 2.68은 첫 실행 때 Logfire 안내 배너를 터미널에 찍는다. 서버 로그를 어지럽히므로 끈다.
    # (PYDANTIC_AI_NO_BANNER 환경변수와 같은 효과. 설치된 버전의 pydantic_ai.BANNER_ENABLED 문서 참고)
    pydantic_ai.BANNER_ENABLED = False
    # 8단계: Logfire. TELEMETRY_ENABLED=true와 토큰이 함께 있을 때만 보내고, 내용(프롬프트·도구 결과)은 넣지 않는다
    # (app/observability.py).
    # 에이전트를 만들기 전에 설정해야 Agent 계측이 처음부터 적용된다.
    configure_observability(settings)
    install_log_redaction()  # 3단계 보강: 앱 로그에 예외 메시지(사용자 글·외부 문장)를 남기지 않는다
    engine = create_engine_for(settings.database_url)
    app = FastAPI(
        title="마이에이전트 API",
        version="0.1.0",
        lifespan=build_lifespan(settings, engine),
        # 문서도 /api 아래에 둔다. 웹의 rewrites(/api/*)로 같은 주소에서 볼 수 있다.
        openapi_url="/api/openapi.json",
        docs_url="/api/docs",
        redoc_url=None,
    )
    app.state.settings = settings
    app.state.session_factory = create_session_factory(engine)
    # 에이전트는 앱마다 한 번, DBOS.launch() 전에 만든다(DBOSDurability가 단계를 등록한다).
    app.state.agent = build_agent(settings, extra_models=agent_models)
    # 감시 에이전트(도구 없음, AGENT_MODEL_GUARD). EXTERNAL 승인 요청마다 한 번 검사한다.
    app.state.guard_agent = build_guard_agent(settings)
    # 감시 판정 에이전트(6단계, 도구 없음, AGENT_MODEL_FAST). 조건 감시가 도구 결과로 조건 충족을 판정한다.
    app.state.watch_agent = build_watch_agent(settings)
    app.state.message_rate_limiter = SlidingWindowRateLimiter()
    app.state.login_rate_limiter = build_login_rate_limiter()
    # 워크플로 시작기와 푸시 전송기. 테스트는 기다리는 시작기와 가짜 전송기로 바꿔 끼운다.
    app.state.launcher = workflows.WorkflowLauncher()
    app.state.push_sender = send_with_pywebpush
    # 외부 API(카카오, 네이버, Open-Meteo, Google)를 부르는 HTTP 클라이언트 하나. 테스트는 가짜 전송으로 바꿔 끼운다.
    app.state.http_client = build_http_client()
    workflows.bind_app_state(app.state)
    add_security_middleware(app, settings)
    app.include_router(api_router)
    instrument_app(app)
    return app
