"""테스트 DB 보호(BUILD_SPEC 12.2: 테스트 DB 연결이 운영 DB를 가리키면 테스트 실행을 거부한다).

테스트는 시작할 때 테스트 DB를 지우고 다시 만든다. 그래서 아래 중 하나라도 걸리면 아무것도 하지 않고 멈춘다.
- DB 이름이 _test로 끝나지 않는다.
- 앱 DB(DATABASE_URL)와 같은 서버·같은 DB다.
- 이 컴퓨터(localhost, 127.0.0.1, ::1)가 아닌 서버에 있다. 원격 DB는 운영일 수 있다.
"""

from sqlalchemy import make_url

LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


class UnsafeTestDatabaseError(RuntimeError):
    """테스트가 지우면 안 되는 DB를 가리킨다."""


def check_test_database(test_url: str, app_url: str | None) -> None:
    test = make_url(test_url)
    name = test.database or ""
    if not name.endswith("_test"):
        raise UnsafeTestDatabaseError(f"테스트 DB 이름은 _test로 끝나야 한다: {name}")
    if test.host not in LOCAL_HOSTS:
        raise UnsafeTestDatabaseError(f"테스트 DB는 이 컴퓨터에 있어야 한다: {test.host}")
    if app_url:
        app = make_url(app_url)
        if (app.host, app.port, app.database) == (test.host, test.port, test.database):
            raise UnsafeTestDatabaseError("테스트 DB가 앱 DB(DATABASE_URL)와 같다")
