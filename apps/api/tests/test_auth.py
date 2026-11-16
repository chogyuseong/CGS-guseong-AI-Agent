import httpx
import pytest

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
