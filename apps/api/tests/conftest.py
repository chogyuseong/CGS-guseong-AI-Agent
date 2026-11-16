"""테스트 공용 픽스처. DB 관련 픽스처는 모두 이 파일에만 둔다.

테스트 DB 전략:
- docker의 postgres 안에 별도 데이터베이스(myagent_test)를 테스트 시작 때 새로 만들고
  Alembic 마이그레이션으로 테이블을 만든다. 마이그레이션 자체도 함께 검증된다.
- 테스트 하나마다 연결 하나에서 바깥 트랜잭션을 연다(db_connection). 앱(요청, 에이전트 실행기, 도구)과
  테스트 코드가 쓰는 세션은 모두 그 연결 위의 SAVEPOINT로 묶이고(session_factory), 테스트가 끝나면 통째로 되돌린다.
- 3단계에서 DBOS가 자체 연결로 DB에 쓰면 이 "되돌리기" 방식으로는 지울 수 없다.
  그때는 db_connection/session_factory 픽스처만 "테이블 비우기(TRUNCATE)" 방식으로 바꾸면 된다.
- 실제 LLM 요청은 전역으로 막는다. 테스트는 agent.override(model=FunctionModel(...))로 가짜 모델을 끼운다.
  감시 에이전트도 guard.override(model=...)로 끼운다. 끼우지 않으면 키가 없어 실패하고 unsure로 기록된다.
- 2단계: fake_external_counter 픽스처가 테스트 전용 EXTERNAL 도구(tests/fake_tools.py)를 chat 도구 묶음에 잠시 넣는다.
  동시 승인 테스트만 committed_app(연결을 따로 여는 실제 커밋 + TRUNCATE 정리)을 쓴다.
"""

import asyncio
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path

import httpx
import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from pydantic import SecretStr
from pydantic_ai import models
from pydantic_settings import SettingsConfigDict
from sqlalchemy import create_engine, make_url, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.agent.agent import MyAgent
from app.agent.guard import GuardAgent
from app.agent.toolsets import (
    TOOL_LABELS,
    TOOL_PREVIEWS,
    TOOLSETS_BY_ORIGIN,
    build_external_toolset,
    internal_tools,
    read_tools,
)
from app.config import Settings
from app.db import get_session
from app.main import create_app
from app.models import User
from app.models.enums import TaskOrigin
from app.security import hash_password
from tests.fake_tools import COUNTER, FakeExternalCounter, fake_external, preview_fake_external

# 테스트에서는 실제 LLM 요청을 전역으로 막는다. 가짜 모델(FunctionModel, TestModel)만 쓴다.
models.ALLOW_MODEL_REQUESTS = False

API_DIR = Path(__file__).resolve().parents[1]
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://myagent:myagent@127.0.0.1:5432/myagent_test",
)
TEST_ORIGIN = "http://localhost:3000"

UserFactory = Callable[[str, str], Awaitable[User]]


@pytest.fixture(scope="session")
def anyio_backend() -> tuple[str, dict[str, object]]:
    # psycopg 비동기는 Windows의 기본 ProactorEventLoop에서 동작하지 않아 SelectorEventLoop를 쓴다.
    return ("asyncio", {"loop_factory": asyncio.SelectorEventLoop})


def recreate_database(url: str) -> None:
    """테스트 DB를 지우고 다시 만든다. 이름이 _test로 끝나지 않으면 거부한다(실수로 개발 DB를 지우지 않게)."""
    db_name = make_url(url).database or ""
    if not db_name.endswith("_test"):
        raise RuntimeError(f"테스트 DB 이름은 _test로 끝나야 한다: {db_name}")
    admin_url = make_url(url).set(database="postgres")
    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with engine.connect() as connection:
        connection.execute(text(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)'))
        connection.execute(text(f'CREATE DATABASE "{db_name}"'))
    engine.dispose()


def upgrade_to_head(url: str) -> None:
    config = Config(str(API_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(API_DIR / "alembic"))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")


@pytest.fixture(scope="session")
def migrated_database_url() -> str:
    recreate_database(TEST_DATABASE_URL)
    upgrade_to_head(TEST_DATABASE_URL)
    return TEST_DATABASE_URL


@pytest.fixture
async def db_connection(migrated_database_url: str) -> AsyncIterator[AsyncConnection]:
    """테스트 하나 동안 쓰는 연결. 바깥 트랜잭션을 열어 두었다가 끝나면 되돌린다."""
    engine = create_async_engine(migrated_database_url, poolclass=NullPool)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        try:
            yield connection
        finally:
            await transaction.rollback()
    await engine.dispose()


@pytest.fixture
def session_factory(db_connection: AsyncConnection) -> async_sessionmaker[AsyncSession]:
    """앱(요청, 에이전트 실행기)이 쓰는 세션 공장. 운영과 같이 세션을 여러 개 만들지만,
    모두 테스트 연결의 SAVEPOINT 안에서 커밋되므로 테스트가 끝나면 함께 되돌아간다."""
    return async_sessionmaker(bind=db_connection, join_transaction_mode="create_savepoint", expire_on_commit=False)


@pytest.fixture
async def db_session(session_factory: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
    """테스트 코드가 데이터를 준비하거나 결과를 확인할 때 쓰는 세션."""
    async with session_factory() as session:
        yield session


class SettingsWithoutEnvFile(Settings):
    """실제 .env를 읽지 않는 설정. 테스트에 실제 키가 섞이지 않게 한다."""

    model_config = SettingsConfigDict(env_file=None)


@pytest.fixture
def test_settings() -> Settings:
    return SettingsWithoutEnvFile(
        database_url=TEST_DATABASE_URL,
        session_secret=SecretStr("test-session-secret-not-for-production"),
        app_origin=TEST_ORIGIN,
    )


@pytest.fixture
def app(test_settings: Settings, session_factory: async_sessionmaker[AsyncSession]) -> FastAPI:
    application = create_app(test_settings)
    # 요청 세션과 에이전트 실행기가 모두 테스트 연결을 쓰게 바꾼다.
    application.state.session_factory = session_factory

    async def override_get_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    application.dependency_overrides[get_session] = override_get_session
    return application


@pytest.fixture
def agent(app: FastAPI) -> MyAgent:
    """앱이 쓰는 에이전트. 테스트는 agent.override(model=FunctionModel(...))로 가짜 모델을 끼운다."""
    return app.state.agent


@pytest.fixture
def guard(app: FastAPI) -> GuardAgent:
    """앱이 쓰는 감시 에이전트. 끼우지 않으면 키가 없어 실패하고 unsure로 기록된다."""
    return app.state.guard_agent


@pytest.fixture
def fake_external_counter(monkeypatch: pytest.MonkeyPatch) -> FakeExternalCounter:
    """chat task의 EXTERNAL 도구 묶음에 테스트 전용 fake_external을 넣는다(테스트가 끝나면 원래대로)."""
    COUNTER.calls.clear()
    external = build_external_toolset([fake_external])
    monkeypatch.setitem(TOOLSETS_BY_ORIGIN, TaskOrigin.CHAT, [read_tools, internal_tools, external])
    monkeypatch.setitem(TOOL_PREVIEWS, "fake_external", preview_fake_external)
    monkeypatch.setitem(TOOL_LABELS, "fake_external", lambda args: "가짜 외부 도구 실행하는 중")
    return COUNTER


def make_client(app: FastAPI) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    headers = {"Origin": TEST_ORIGIN}
    return httpx.AsyncClient(transport=transport, base_url="http://testserver", headers=headers)


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with make_client(app) as http:
        yield http


# ---- 실제로 커밋하는 DB (동시 요청 테스트 전용) ----
# 위의 session_factory는 연결 하나를 공유하므로 두 요청이 진짜로 동시에 DB를 잠그는 상황을 만들 수 없다.
# 동시 승인 테스트만 연결을 따로 여는 엔진을 쓰고, 끝나면 users를 TRUNCATE ... CASCADE로 비운다.
# 이 픽스처는 db_connection(바깥 트랜잭션) 픽스처와 같은 테스트에서 함께 쓰지 않는다(TRUNCATE가 잠금을 기다린다).


@pytest.fixture
async def committed_app(migrated_database_url: str, test_settings: Settings) -> AsyncIterator[FastAPI]:
    engine = create_async_engine(migrated_database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    application = create_app(test_settings)
    application.state.session_factory = factory

    async def override_get_session() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    application.dependency_overrides[get_session] = override_get_session
    try:
        yield application
    finally:
        async with engine.begin() as connection:
            await connection.execute(text("TRUNCATE users RESTART IDENTITY CASCADE"))
        await engine.dispose()


@pytest.fixture
def make_user(db_session: AsyncSession) -> UserFactory:
    async def _make_user(email: str, password: str) -> User:
        user = User(email=email, password_hash=hash_password(password))
        db_session.add(user)
        await db_session.flush()
        return user

    return _make_user


async def login(client: httpx.AsyncClient, email: str, password: str) -> httpx.Response:
    return await client.post("/api/auth/login", json={"email": email, "password": password})


@pytest.fixture
async def logged_in_user(client: httpx.AsyncClient, make_user: UserFactory) -> User:
    """로그인까지 마친 사용자 한 명."""
    user = await make_user("me@example.com", "password-me")
    response = await login(client, "me@example.com", "password-me")
    assert response.status_code == 200
    return user


@pytest.fixture
async def conversation(client: httpx.AsyncClient, logged_in_user: User) -> int:
    """로그인한 사용자의 대화방 하나를 API로 만들고 id를 돌려준다."""
    response = await client.post("/api/conversations", json={"title": "테스트 대화"})
    assert response.status_code == 201
    return response.json()["id"]
