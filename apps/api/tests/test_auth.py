from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import UserSession
from app.routers.auth import LOGIN_ATTEMPTS_PER_MINUTE
from app.security import SESSION_COOKIE_NAME
from tests.conftest import UserFactory, login

EMAIL = "me@example.com"
PASSWORD = "correct-horse-battery"


async def test_login_success_sets_session_cookie(client: httpx.AsyncClient, make_user: UserFactory) -> None:
    user = await make_user(EMAIL, PASSWORD)

    response = await login(client, EMAIL, PASSWORD)

    assert response.status_code == 200
    assert response.json() == {"id": user.id, "email": EMAIL}
    cookie_header = response.headers["set-cookie"].lower()
    assert "httponly" in cookie_header
    assert "samesite=lax" in cookie_header

    me = await client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json() == {"id": user.id, "email": EMAIL}


async def test_login_with_wrong_password_returns_401(client: httpx.AsyncClient, make_user: UserFactory) -> None:
    await make_user(EMAIL, PASSWORD)

    response = await login(client, EMAIL, "wrong-password")

    assert response.status_code == 401
    assert "set-cookie" not in response.headers


async def test_me_without_login_returns_401(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/auth/me")

    assert response.status_code == 401


@pytest.mark.parametrize("origin", ["http://evil.example.com", None])
async def test_post_with_other_origin_returns_403(
    client: httpx.AsyncClient, make_user: UserFactory, origin: str | None
) -> None:
    await make_user(EMAIL, PASSWORD)
    headers = {"Origin": origin} if origin else {}
    if origin is None:
        client.headers.pop("Origin")

    response = await client.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD}, headers=headers)

    assert response.status_code == 403
    assert "set-cookie" not in response.headers


# ---- 0단계 보강(BUILD_SPEC 10.3): 서버 저장형 세션, 로그인 속도 제한 ----


async def test_logout_invalidates_stolen_cookie(client: httpx.AsyncClient, make_user: UserFactory) -> None:
    """로그아웃하면 서버의 세션이 지워져, 미리 복사해 둔 쿠키로도 들어올 수 없다."""
    await make_user(EMAIL, PASSWORD)
    await login(client, EMAIL, PASSWORD)
    stolen = client.cookies.get(SESSION_COOKIE_NAME)
    assert stolen

    await client.post("/api/auth/logout")
    client.cookies.set(SESSION_COOKIE_NAME, stolen)

    assert (await client.get("/api/auth/me")).status_code == 401


async def test_login_rotates_session(client: httpx.AsyncClient, make_user: UserFactory) -> None:
    """다시 로그인하면 이전 세션 토큰은 쓸 수 없다(세션 고정 방지)."""
    await make_user(EMAIL, PASSWORD)
    await login(client, EMAIL, PASSWORD)
    old = client.cookies.get(SESSION_COOKIE_NAME)

    await login(client, EMAIL, PASSWORD)
    new = client.cookies.get(SESSION_COOKIE_NAME)
    assert new and new != old
    client.cookies.set(SESSION_COOKIE_NAME, old or "")

    assert (await client.get("/api/auth/me")).status_code == 401


async def test_db_stores_only_token_hash(
    client: httpx.AsyncClient, make_user: UserFactory, db_session: AsyncSession
) -> None:
    """DB가 새어도 쿠키 값을 알 수 없게 토큰 원문 대신 해시만 저장한다."""
    user = await make_user(EMAIL, PASSWORD)
    await login(client, EMAIL, PASSWORD)
    token = client.cookies.get(SESSION_COOKIE_NAME) or ""

    rows = (await db_session.scalars(select(UserSession).where(UserSession.user_id == user.id))).all()

    assert len(rows) == 1
    assert rows[0].token_hash != token
    assert token not in rows[0].token_hash


async def test_expired_session_is_rejected(
    client: httpx.AsyncClient, make_user: UserFactory, db_session: AsyncSession
) -> None:
    user = await make_user(EMAIL, PASSWORD)
    await login(client, EMAIL, PASSWORD)
    await db_session.execute(
        update(UserSession)
        .where(UserSession.user_id == user.id)
        .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    await db_session.commit()

    assert (await client.get("/api/auth/me")).status_code == 401


async def test_login_attempts_are_rate_limited(client: httpx.AsyncClient, make_user: UserFactory) -> None:
    """같은 이메일로 1분에 5번을 넘게 시도하면 비밀번호가 맞아도 429."""
    await make_user(EMAIL, PASSWORD)
    for _ in range(LOGIN_ATTEMPTS_PER_MINUTE):
        assert (await login(client, EMAIL, "wrong-password")).status_code == 401

    response = await login(client, EMAIL, PASSWORD)

    assert response.status_code == 429
    assert "set-cookie" not in response.headers


async def test_unknown_email_is_rate_limited_the_same_way(client: httpx.AsyncClient) -> None:
    """없는 계정도 같은 한도로 막아 응답으로 계정 존재를 알 수 없게 한다."""
    for _ in range(LOGIN_ATTEMPTS_PER_MINUTE):
        assert (await login(client, "nobody@example.com", "x")).status_code == 401

    assert (await login(client, "Nobody@Example.com", "x")).status_code == 429


async def test_successful_logins_are_not_counted(client: httpx.AsyncClient, make_user: UserFactory) -> None:
    """한도는 무차별 대입을 막는 것이라 실패한 시도만 센다. 성공한 로그인을 여러 번 해도 막히지 않는다."""
    await make_user(EMAIL, PASSWORD)
    for _ in range(LOGIN_ATTEMPTS_PER_MINUTE + 2):
        assert (await login(client, EMAIL, PASSWORD)).status_code == 200
