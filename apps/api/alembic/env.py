"""Alembic 실행 환경.

- 마이그레이션은 동기 엔진으로 돌린다. psycopg 3는 같은 URL(postgresql+psycopg://)로 동기·비동기를 모두 지원하므로
  앱은 비동기, 마이그레이션은 동기로 써도 드라이버는 하나다. 동기라서 Windows 이벤트 루프 문제도 없다.
- 주소는 alembic.ini에 적힌 값(테스트가 넣음)이 있으면 그것을, 없으면 .env의 DATABASE_URL을 쓴다.
- alembic.ini는 영어(ASCII)로만 쓴다. Alembic이 OS 로캘 인코딩(한국어 Windows는 cp949)으로 읽어서
  UTF-8 한국어 주석이 있으면 읽다가 실패한다.
- alembic.ini의 post_write_hooks로 새 마이그레이션 파일에 ruff 검사·포맷을 자동 적용한다.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from app.config import get_settings
from app.models import Base

config = context.config

if config.config_file_name is not None:
    # 테스트 중에 pytest의 로거를 끄지 않도록 기존 로거를 유지한다.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def get_database_url() -> str:
    return config.get_main_option("sqlalchemy.url") or get_settings().database_url


def run_migrations_offline() -> None:
    """DB에 연결하지 않고 SQL 문만 출력한다(alembic upgrade head --sql)."""
    context.configure(
        url=get_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(get_database_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
