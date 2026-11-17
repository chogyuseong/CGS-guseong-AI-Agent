"""테스트 공용 픽스처. DB 관련 픽스처는 모두 이 파일에만 둔다.

테스트 DB 전략:
- docker의 postgres 안에 별도 데이터베이스(myagent_test)를 테스트 시작 때 새로 만들고
  Alembic 마이그레이션으로 테이블을 만든다. 마이그레이션 자체도 함께 검증된다.
- 테스트 하나마다 연결 하나에서 바깥 트랜잭션을 연다(db_connection). 앱(요청, 에이전트 실행기, 도구)과
  테스트 코드가 쓰는 세션은 모두 그 연결 위의 SAVEPOINT로 묶이고(session_factory), 테스트가 끝나면 통째로 되돌린다.
- 실제 LLM 요청은 전역으로 막는다. 테스트는 agent.override(model=scripted(...).model())로 가짜 모델을 끼운다.
  워크플로 안에서는 DBOSDurability에 등록된 모델만 끼울 수 있어서, 앱을 만들 때 TEST_AGENT_MODELS를 등록한다
  (tests/model_scripts.py). 감시 에이전트는 DBOS를 쓰지 않으므로 guard.override(model=...)로 아무 모델이나 끼운다.
- 2단계: fake_external_counter 픽스처가 테스트 전용 EXTERNAL 도구(tests/fake_tools.py)를 chat 도구 묶음에 잠시 넣는다.
  동시 승인 테스트처럼 진짜 동시성이 필요한 테스트는 committed_app(연결을 따로 여는 실제 커밋 + TRUNCATE 정리)을 쓴다.

3단계(DBOS):
- 세션 시작 때 DBOS를 한 번 만들고 launch한다(dbos_runtime). 시스템 테이블은 테스트 DB의 dbos 스키마에 생긴다.
  DBOS는 자기 연결(동기 엔진)로 dbos 스키마에만 쓰므로 앱 테이블의 SAVEPOINT 되돌리기는 그대로 쓸 수 있다.
  dbos 스키마의 행은 되돌려지지 않지만, 테스트 DB는 세션마다 새로 만들고 task id 시퀀스도 되돌려지지 않아
  (그래서 committed_app의 TRUNCATE에서 RESTART IDENTITY를 뺐다) 같은 워크플로 ID("task-{id}")가 다시 나오지 않는다.
- 앱의 워크플로 시작기는 WaitingLauncher로 바꾼다. 실제 워크플로를 시작하되 끝날 때까지 기다려서, 테스트는 응답을
  받은 뒤 결과를 바로 확인할 수 있고 연결 하나를 두 코루틴이 동시에 쓰지 않는다. "바로 응답"을 확인하는 테스트만
  committed_app + 기다리지 않는 시작기를 쓴다.
- 푸시 전송기는 FakePushSender(tests/fake_push.py)로 바꾼다. VAPID 설정이 비어 있으면 notify는 아무것도 보내지 않는다.

4단계(외부 API):
- fake_http 픽스처가 앱의 HTTP 클라이언트를 가짜 전송(tests/fake_http.py)으로 바꾸고 가짜 키를 설정에 넣는다.
  외부로 나가는 요청은 없다. 등록하지 않은 주소로 요청이 가면 테스트가 실패한다.
- tool_deps는 도구 함수를 직접 부를 때 쓰는 deps(로그인한 사용자의 chat task)다. 응답은 tests/fixtures/(README.md 참고).
"""

import asyncio
import os
import sys
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
from alembic import command
from alembic.config import Config
from cryptography.fernet import Fernet
from dbos import DBOS, WorkflowHandleAsync
from dotenv import dotenv_values
from fastapi import FastAPI
from pydantic import SecretStr
from pydantic_ai import RunContext, models
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RunUsage
from pydantic_settings import SettingsConfigDict
from sqlalchemy import create_engine, make_url, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.agent.agent import MyAgent
from app.agent.budget import seoul_today
from app.agent.deps import AgentDeps
from app.agent.guard import GuardAgent
from app.agent.results import ToolResult
from app.agent.toolsets import (
    EXTERNAL_TOOL_SPECS,
    TOOL_LABELS,
    TOOL_PREVIEWS,
    TOOLSETS_BY_ORIGIN,
    build_external_toolset,
    internal_tools,
    read_tools,
)
from app.agent.workflows import DBOS_SYSTEM_SCHEMA, WorkflowLauncher, automation_services_from_state
from app.automation.check import AutomationServices
from app.automation.watch import WatchAgent
from app.config import MODEL_PRICES, REPO_ROOT, ModelPrice, Settings
from app.db import get_session
from app.main import create_app
from app.models import Conversation, DailyBudget, Task, User
from app.models.enums import TaskOrigin, TaskStatus
from app.security import hash_password
from tests.db_guard import check_test_database
from tests.fake_http import FakeHttp
from tests.fake_push import FakePushSender
from tests.fake_service import FakeExternalService
from tests.fake_tools import (
    COUNTER,
    FAKE_EXTERNAL_SPEC,
    HOLDER,
    FakeExternalCounter,
    fake_external,
    preview_fake_external,
)
from tests.model_scripts import TEST_AGENT_MODELS

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


def app_database_url() -> str | None:
    """앱이 쓰는 DATABASE_URL(환경변수 우선, 없으면 루트 .env). 테스트 DB와 같은지 비교하는 데만 쓴다."""
    return os.environ.get("DATABASE_URL") or dotenv_values(REPO_ROOT / ".env").get("DATABASE_URL")


def recreate_database(url: str) -> None:
    """테스트 DB를 지우고 다시 만든다. 지우면 안 되는 DB면 거부한다(tests/db_guard.py)."""
    check_test_database(url, app_database_url())
    db_name = make_url(url).database or ""
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


@pytest.fixture(scope="session")
def dbos_runtime(migrated_database_url: str) -> Iterator[None]:
    """테스트 세션 동안 DBOS 하나를 띄운다. 시스템 테이블은 테스트 DB의 dbos 스키마에 생긴다.

    이벤트 루프 밖에서 launch하므로 DBOS가 자기 백그라운드 루프를 만든다(fork·큐로 시작한 워크플로가 거기서 돈다).
    Windows 기본 루프(Proactor)는 psycopg 비동기를 못 쓰므로 그 루프도 SelectorEventLoop가 되게 정책을 바꾼다.
    (운영 서버는 실행 중인 루프 안에서 launch하므로 DBOS가 그 루프를 쓴다. app/agent/workflows.py)
    """
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    DBOS(
        config={
            "name": "myagent-test",
            "system_database_url": migrated_database_url,
            "dbos_system_schema": DBOS_SYSTEM_SCHEMA,
            "application_version": "test",
        }
    )
    DBOS.launch()
    try:
        yield
    finally:
        DBOS.destroy()


class WaitingLauncher(WorkflowLauncher):
    """실제 DBOS 워크플로를 시작하고 끝날 때까지 기다리는 시작기(테스트 전용)."""

    async def start_run(self, task_id: int, prompt: str) -> WorkflowHandleAsync[None]:
        handle = await super().start_run(task_id, prompt)
        await handle.get_result()
        return handle

    async def start_resume(self, task_id: int, workflow_id: str) -> WorkflowHandleAsync[None]:
        handle = await super().start_resume(task_id, workflow_id)
        await handle.get_result()
        return handle

    async def start_schedule(self, job_id: int, run_at: datetime, checked_at: datetime) -> WorkflowHandleAsync[None]:
        handle = await super().start_schedule(job_id, run_at, checked_at)
        await handle.get_result()
        return handle

    async def start_watch(self, watch_id: int, run_at: datetime, checked_at: datetime) -> WorkflowHandleAsync[None]:
        handle = await super().start_watch(watch_id, run_at, checked_at)
        await handle.get_result()
        return handle


def configure_test_app(application: FastAPI, factory: async_sessionmaker[AsyncSession]) -> None:
    """앱의 DB 세션, 워크플로 시작기, 푸시 전송기를 테스트용으로 바꾼다."""
    application.state.session_factory = factory
    application.state.launcher = WaitingLauncher()
    application.state.push_sender = FakePushSender()

    async def override_get_session() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    application.dependency_overrides[get_session] = override_get_session


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
def app(test_settings: Settings, session_factory: async_sessionmaker[AsyncSession], dbos_runtime: None) -> FastAPI:
    application = create_app(test_settings, agent_models=TEST_AGENT_MODELS)
    # 요청 세션과 에이전트 실행기(워크플로)가 모두 테스트 연결을 쓰게 바꾼다.
    configure_test_app(application, session_factory)
    return application


@pytest.fixture
def push_sender(app: FastAPI) -> FakePushSender:
    """앱이 쓰는 가짜 푸시 전송기. 보낸 본문을 기록한다."""
    return app.state.push_sender


@pytest.fixture
def agent(app: FastAPI) -> MyAgent:
    """앱이 쓰는 에이전트. 테스트는 agent.override(model=FunctionModel(...))로 가짜 모델을 끼운다."""
    return app.state.agent


@pytest.fixture
def guard(app: FastAPI) -> GuardAgent:
    """앱이 쓰는 감시 에이전트. 끼우지 않으면 키가 없어 실패하고 unsure로 기록된다."""
    return app.state.guard_agent


@pytest.fixture
def watch_agent(app: FastAPI) -> WatchAgent:
    """앱이 쓰는 감시 판정 에이전트(6단계). 테스트는 watch_agent.override(model=...)로 가짜 판정을 끼운다."""
    return app.state.watch_agent


@pytest.fixture
def automation_services(app: FastAPI) -> AutomationServices:
    """1분 검사(run_check)를 테스트에서 직접 부를 때 쓰는 실행 도구. 시작기는 끝날 때까지 기다린다."""
    return automation_services_from_state(app.state)


@pytest.fixture
def fake_service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeExternalService:
    """가짜 외부 서비스(SQLite 파일, 테스트마다 새 파일). fake_external이 이 서비스로 요청한다(2단계 보강)."""
    service = FakeExternalService(tmp_path / "fake_external.sqlite3")
    monkeypatch.setattr(HOLDER, "service", service)
    return service


@pytest.fixture
def fake_external_counter(monkeypatch: pytest.MonkeyPatch, fake_service: FakeExternalService) -> FakeExternalCounter:
    """chat task의 EXTERNAL 도구 묶음에 테스트 전용 fake_external을 넣는다(테스트가 끝나면 원래대로)."""
    COUNTER.calls.clear()
    external = build_external_toolset([fake_external])
    monkeypatch.setitem(TOOLSETS_BY_ORIGIN, TaskOrigin.CHAT, [read_tools, internal_tools, external])
    monkeypatch.setitem(TOOL_PREVIEWS, "fake_external", preview_fake_external)
    monkeypatch.setitem(EXTERNAL_TOOL_SPECS, "fake_external", FAKE_EXTERNAL_SPEC)
    monkeypatch.setitem(TOOL_LABELS, "fake_external", lambda args: "가짜 외부 도구 실행하는 중")
    return COUNTER


# ---- 4단계: 외부 API (가짜 HTTP와 테스트 키) ----
# 실제 .env의 키는 쓰지 않는다. 아래 값은 가짜이고 외부로 나가지 않는다(FakeHttp가 요청을 가로챈다).
TEST_FERNET_KEY = Fernet.generate_key().decode("ascii")
TEST_GOOGLE_REDIRECT = f"{TEST_ORIGIN}/api/connections/google/callback"


def configure_external_keys(settings: Settings) -> None:
    settings.kakao_rest_api_key = SecretStr("test-kakao-key")
    settings.naver_client_id = "test-naver-id"
    settings.naver_client_secret = SecretStr("test-naver-secret")
    settings.google_client_id = "test-google-client.apps.googleusercontent.com"
    settings.google_client_secret = SecretStr("test-google-secret")
    settings.google_redirect_uri = TEST_GOOGLE_REDIRECT
    settings.token_encryption_key = SecretStr(TEST_FERNET_KEY)


@pytest.fixture
def fake_http(app: FastAPI, test_settings: Settings) -> FakeHttp:
    """앱(워크플로의 도구 포함)이 쓰는 HTTP 클라이언트를 가짜로 바꾸고 테스트 키를 넣는다."""
    fake = FakeHttp()
    app.state.http_client = fake.client()
    configure_external_keys(test_settings)
    return fake


@pytest.fixture
async def tool_deps(
    fake_http: FakeHttp,
    test_settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    db_session: AsyncSession,
    logged_in_user: User,
) -> AgentDeps:
    """도구 함수를 직접 부를 때 쓰는 deps(로그인한 사용자의 chat task 하나)."""
    conversation = Conversation(user_id=logged_in_user.id, title="도구 직접 호출")
    db_session.add(conversation)
    await db_session.flush()
    task = Task(
        conversation_id=conversation.id, user_id=logged_in_user.id, origin=TaskOrigin.CHAT, status=TaskStatus.RUNNING
    )
    db_session.add(task)
    await db_session.commit()
    return AgentDeps(
        user_id=logged_in_user.id,
        task_id=task.id,
        origin=TaskOrigin.CHAT,
        db=session_factory,
        settings=test_settings,
        http=fake_http.client(),
    )


def payload(result: ToolResult) -> Any:
    """ToolResult.data(JsonValue)를 테스트에서 키로 꺼내 보기 위한 도우미(타입 검사용)."""
    return result.data


def tool_context(deps: AgentDeps, tool_name: str, tool_call_id: str = "call-direct") -> RunContext[AgentDeps]:
    return RunContext(deps=deps, model=TestModel(), usage=RunUsage(), tool_name=tool_name, tool_call_id=tool_call_id)


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
async def committed_app(
    migrated_database_url: str, test_settings: Settings, dbos_runtime: None
) -> AsyncIterator[FastAPI]:
    engine = create_async_engine(migrated_database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    application = create_app(test_settings, agent_models=TEST_AGENT_MODELS)
    configure_test_app(application, factory)
    try:
        yield application
    finally:
        # RESTART IDENTITY를 쓰지 않는다: task id가 다시 1부터 나오면 dbos 스키마에 남은 "task-1" 워크플로와 겹친다.
        async with engine.begin() as connection:
            await connection.execute(text("TRUNCATE users CASCADE"))
        await engine.dispose()


@pytest.fixture
async def make_committed_user(migrated_database_url: str) -> AsyncIterator[UserFactory]:
    """실제로 커밋되는 사용자(동시성 테스트용). 끝나면 users를 TRUNCATE ... CASCADE로 비운다."""
    engine = create_async_engine(migrated_database_url, poolclass=NullPool)

    async def _make(email: str, password: str) -> User:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            user = User(email=email, password_hash=hash_password(password))
            session.add(user)
            await session.commit()
            return user

    try:
        yield _make
    finally:
        async with engine.begin() as connection:
            await connection.execute(text("TRUNCATE users CASCADE"))
        await engine.dispose()


def spent_today(user_id: int, amount: Decimal) -> DailyBudget:
    """오늘(서울) 이미 amount만큼 쓴 원장 행. 하루 한도 테스트에서 db_session.add()로 넣는다."""
    return DailyBudget(user_id=user_id, local_date=seoul_today(), spent_usd=amount, reserved_usd=Decimal(0))


# 1단계 보강: 모델 호출마다 가격표로 예산을 잡으므로(가격 없는 모델은 부르지 않음) 가짜 모델에도 가격을 준다.
TEST_MODEL_PRICE = ModelPrice(input_per_mtok=Decimal("1"), output_per_mtok=Decimal("5"))
TEST_MODEL_NAMES = ("scripted", "guard-scripted", "watch-scripted", "watch-broken", "test", "function")


@pytest.fixture(autouse=True)
def test_model_prices(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in TEST_MODEL_NAMES:
        monkeypatch.setitem(MODEL_PRICES, name, TEST_MODEL_PRICE)


@pytest.fixture
def make_user(db_session: AsyncSession) -> UserFactory:
    async def _make_user(email: str, password: str) -> User:
        user = User(email=email, password_hash=hash_password(password))
        db_session.add(user)
        await db_session.flush()
        return user

    return _make_user


def message_body(content: str) -> dict[str, str]:
    """메시지 전송 본문. 화면처럼 보낼 때마다 새 client_request_id를 붙인다."""
    return {"content": content, "client_request_id": str(uuid.uuid4())}


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
