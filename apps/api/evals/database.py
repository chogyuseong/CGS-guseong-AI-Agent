"""eval 전용(버리는) DB. 개발 DB와 같은 PostgreSQL 안에 이름이 _eval로 끝나는 DB를 만들고 끝나면 지운다.

이름 검사(check_eval_database_name) 때문에 개발 DB(myagent)나 테스트 DB(myagent_test)를 지울 수 없다.
DBOS는 이 DB에 띄우지 않는다(eval 실행기는 워크플로를 쓰지 않는다).

흐름 속 위치: run_evals.evaluate가 사례를 돌리기 전에 create_eval_database, 끝나면(실패해도 finally)
drop_eval_database를 부른다. harness는 이 DB의 세션만 받는다.
개발 DB에는 run_evals가 마지막에 usage_logs(purpose=eval)만 쓴다.

근거: DECISIONS 2026-11-17(eval DB는 버리는 DB myagent_eval. 개발 DB에 전용 계정을 두는 것보다 정리가 확실하고
개발 데이터와 섞이지 않는다).

고칠 때 주의
- EVAL_DATABASE 이름을 바꾸면 반드시 _eval로 끝나야 한다(아니면 check_eval_database_name이 거부한다).
  docs/EVALS.md '외부에 흔적을 남기지 않는 장치'와 AGENTS.md 실행 절의 이름도 같이 고친다.
- 이름 검사를 빼거나 느슨하게 하면 DROP DATABASE ... WITH (FORCE)가 개발 DB를 지울 수 있다. 검사는 지우기·만들기
  각각의 첫 줄에 둔다.
- tests/test_evals.py의 test_eval_database_is_created_with_tables_and_dropped가 만들기·지우기를 실제 DB로 확인한다.

변경 이력
- 2026-11-17 처음 작성(8단계).
- 2026-11-18 주석 보강(동작 변경 없음).
"""

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, make_url, text

from evals.harness import check_eval_database_name

API_DIR = Path(__file__).resolve().parents[1]
# 개발 DB(myagent)·테스트 DB(myagent_test)와 겹치지 않고 _eval로 끝나는 이름(DECISIONS 2026-11-17).
EVAL_DATABASE = "myagent_eval"


def eval_database_url(app_database_url: str) -> str:
    """개발 DB 주소에서 이름만 myagent_eval로 바꾼다(같은 서버·같은 계정).

    비밀번호를 가리지 않은 문자열을 돌려준다(엔진·Alembic에 그대로 넘기려고). 화면·로그에 출력하지 않는다.
    """
    url = make_url(app_database_url).set(database=EVAL_DATABASE)
    return url.render_as_string(hide_password=False)


def admin_engine(url: str):
    """DB를 만들고 지우기 위한 엔진. 지울 DB 자신에는 붙을 수 없어 postgres DB에 붙는다.

    CREATE/DROP DATABASE는 트랜잭션 안에서 실행할 수 없어 AUTOCOMMIT으로 연다. 쓴 뒤 dispose로 닫는다.
    """
    return create_engine(make_url(url).set(database="postgres"), isolation_level="AUTOCOMMIT")


def drop_eval_database(url: str) -> None:
    """eval DB를 지운다(없으면 아무 일 없음). 부수 효과: DB 삭제, 남은 연결은 FORCE로 끊는다.

    이름 검사가 먼저다: 주소를 잘못 넘겨 개발 DB를 지우는 사고를 막는다.
    """
    name = check_eval_database_name(url)
    engine = admin_engine(url)
    with engine.connect() as connection:
        connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    engine.dispose()


def create_eval_database(url: str) -> None:
    """지우고 새로 만든 뒤 Alembic으로 앱 테이블을 만든다(이전 eval의 남은 것이 섞이지 않게).

    부수 효과: 기존 eval DB 삭제, 새 DB 생성, 마이그레이션 head까지 적용. 이름 검사로 개발 DB를 건드리지 않는다.
    DBOS 스키마는 만들지 않는다(eval은 워크플로를 쓰지 않는다).
    """
    name = check_eval_database_name(url)
    drop_eval_database(url)
    engine = admin_engine(url)
    with engine.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    engine.dispose()
    config = Config(str(API_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(API_DIR / "alembic"))
    # Alembic Config는 configparser라 %를 보간 문자로 읽는다(비밀번호의 %가 깨지지 않게 두 번 쓴다).
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    command.upgrade(config, "head")
