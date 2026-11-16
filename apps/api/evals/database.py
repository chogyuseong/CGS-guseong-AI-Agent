"""eval 전용(버리는) DB. 개발 DB와 같은 PostgreSQL 안에 이름이 _eval로 끝나는 DB를 만들고 끝나면 지운다.

이름 검사(check_eval_database_name) 때문에 개발 DB(myagent)나 테스트 DB(myagent_test)를 지울 수 없다.
DBOS는 이 DB에 띄우지 않는다(eval 실행기는 워크플로를 쓰지 않는다).
"""

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, make_url, text

from evals.harness import check_eval_database_name

API_DIR = Path(__file__).resolve().parents[1]
EVAL_DATABASE = "myagent_eval"


def eval_database_url(app_database_url: str) -> str:
    """개발 DB 주소에서 이름만 myagent_eval로 바꾼다(같은 서버·같은 계정)."""
    url = make_url(app_database_url).set(database=EVAL_DATABASE)
    return url.render_as_string(hide_password=False)


def admin_engine(url: str):
    return create_engine(make_url(url).set(database="postgres"), isolation_level="AUTOCOMMIT")


def drop_eval_database(url: str) -> None:
    name = check_eval_database_name(url)
    engine = admin_engine(url)
    with engine.connect() as connection:
        connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    engine.dispose()


def create_eval_database(url: str) -> None:
    """지우고 새로 만든 뒤 Alembic으로 앱 테이블을 만든다(이전 eval의 남은 것이 섞이지 않게)."""
    name = check_eval_database_name(url)
    drop_eval_database(url)
    engine = admin_engine(url)
    with engine.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    engine.dispose()
    config = Config(str(API_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(API_DIR / "alembic"))
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    command.upgrade(config, "head")
