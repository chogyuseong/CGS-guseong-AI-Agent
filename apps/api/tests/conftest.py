"""테스트 공용 픽스처. DB 관련 픽스처는 모두 이 파일에만 둔다.

테스트 DB 전략 (0단계):
- docker의 postgres 안에 별도 데이터베이스(myagent_test)를 테스트 시작 때 새로 만들고
  Alembic 마이그레이션으로 테이블을 만든다. 마이그레이션 자체도 함께 검증된다.
- 테스트 하나마다 연결 하나에서 트랜잭션을 열고, 앱의 세션을 그 트랜잭션 안의 SAVEPOINT로 묶은 뒤
  테스트가 끝나면 통째로 되돌린다. 테스트끼리 데이터가 섞이지 않는다.
- 3단계에서 DBOS가 자체 연결로 DB에 쓰면 이 "되돌리기" 방식으로는 지울 수 없다.
  그때는 db_session 픽스처만 "테이블 비우기(TRUNCATE)" 방식으로 바꾸면 된다.
"""

import asyncio
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path

import httpx
import pytest
from alembic import command
from alembic.config import Config
from pydantic import SecretStr
from pydantic_settings import SettingsConfigDict
from sqlalchemy import create_engine, make_url, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import Settings
from app.db import get_session
from app.main import create_app
from app.models import User
from app.security import hash_password

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
async def db_session(migrated_database_url: str) -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(migrated_database_url, poolclass=NullPool)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(
            bind=connection,
            join_transaction_mode="create_savepoint",
            expire_on_commit=False,
        )
        try:
            yield session
        finally:
            await session.close()
            await transaction.rollback()
    await engine.dispose()


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
async def client(test_settings: Settings, db_session: AsyncSession) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(test_settings)

    async def override_get_session() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = override_get_session
    transport = httpx.ASGITransport(app=app)
    headers = {"Origin": TEST_ORIGIN}
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver", headers=headers) as http:
        yield http


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
