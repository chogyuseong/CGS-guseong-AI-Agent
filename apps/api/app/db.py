"""DB 연결. SQLAlchemy 2.1 비동기 엔진과 요청마다 세션 하나를 주는 의존성.

드라이버는 psycopg 3(postgresql+psycopg://)다. 같은 URL로 create_async_engine은 비동기,
create_engine은 동기 드라이버를 고르므로 앱(비동기), Alembic(동기), 3단계의 DBOS(psycopg 3 고정)가
한 드라이버를 같이 쓴다. 이유는 docs/DECISIONS.md.
"""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine


def create_engine_for(database_url: str) -> AsyncEngine:
    # pool_pre_ping: DB 컨테이너를 재시작해도 끊긴 연결을 버리고 다시 연결한다.
    return create_async_engine(database_url, pool_pre_ping=True)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    # expire_on_commit=False: 커밋 뒤에도 객체 속성을 읽을 수 있게 한다(응답을 만들 때 다시 조회하지 않도록).
    return async_sessionmaker(engine, expire_on_commit=False)


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """FastAPI 의존성: 요청 하나에 세션 하나. Django의 요청별 DB 연결과 비슷하다."""
    session_factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with session_factory() as session:
        yield session


# 라우터 함수 인자에 `session: DbSession`으로 쓰면 요청마다 세션이 들어온다.
DbSession = Annotated[AsyncSession, Depends(get_session)]
