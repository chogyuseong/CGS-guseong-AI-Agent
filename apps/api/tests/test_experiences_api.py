"""내 경험 API 테스트(생활 비서 D 첫 단계, 2026-11-18 사용자 결정: 원문 암호화 + 등록 전 안내, 설정 화면에서 등록).

- 본문은 PAYLOAD_ENCRYPTION_KEY로 암호화해 저장한다(DB에 평문 없음). 키가 없으면 등록·본문 보기를 막는다(503).
- 연락처(전화·이메일)·주소가 들어 있으면 저장하지 않는다(값은 응답에 되풀이하지 않음).
- 남의 경험은 없는 것과 같이 404. 20개 한도.
설계: docs/superpowers/specs/2026-11-18-job-prep-design.md
"""

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models import Experience, User
from app.tools import experiences
from tests.conftest import TEST_PAYLOAD_KEY, UserFactory, login

BODY = "SSAFY 2학기 프로젝트에서 FastAPI로 일정 비서를 만들었다. 승인 흐름과 실행 영수증을 설계했다."


@pytest.fixture
def payload_key(test_settings: Settings) -> Settings:
    test_settings.payload_encryption_key = SecretStr(TEST_PAYLOAD_KEY)
    return test_settings


async def add(client: httpx.AsyncClient, title: str = "마이에이전트 프로젝트", body: str = BODY) -> httpx.Response:
    return await client.post("/api/experiences", json={"title": title, "body": body})


async def test_create_stores_body_encrypted_and_lists_without_body(
    client: httpx.AsyncClient, db_session: AsyncSession, logged_in_user: User, payload_key: Settings
) -> None:
    created = await add(client)

    assert created.status_code == 201, created.text
    listing = (await client.get("/api/experiences")).json()
    assert listing["enabled"] is True and listing["limit"] == experiences.MAX_EXPERIENCES
    [item] = listing["experiences"]
    assert set(item) == {"id", "title", "length", "created_at"}
    assert item["title"] == "마이에이전트 프로젝트" and item["length"] == len(BODY)
    stored = await db_session.scalar(select(Experience).where(Experience.id == item["id"]))
    assert stored is not None and "FastAPI" not in stored.body_encrypted
    detail = (await client.get(f"/api/experiences/{item['id']}")).json()
    assert detail["body"] == BODY


@pytest.mark.parametrize(
    ("body", "kind"),
    [
        ("연락은 010-1234-5678로 주세요.", "전화번호"),
        ("메일 me@example.com 으로 보내 주세요.", "이메일"),
        ("서울시 강남구 테헤란로 212 에서 교육을 받았다.", "주소"),
        ("101동 1203호에 산다.", "주소"),
    ],
)
async def test_contact_or_address_is_refused_without_echoing_value(
    client: httpx.AsyncClient, logged_in_user: User, payload_key: Settings, body: str, kind: str
) -> None:
    response = await add(client, body=body)

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert kind in detail
    assert "5678" not in detail and "example.com" not in detail and "212" not in detail
    assert (await client.get("/api/experiences")).json()["experiences"] == []


async def test_ordinary_words_like_route_are_not_mistaken_for_address(
    client: httpx.AsyncClient, logged_in_user: User, payload_key: Settings
) -> None:
    response = await add(client, body="최단 경로 3개를 비교하고 대로 2곳의 혼잡도를 계산했다.")

    assert response.status_code == 201


async def test_without_payload_key_registration_is_disabled(client: httpx.AsyncClient, logged_in_user: User) -> None:
    listing = (await client.get("/api/experiences")).json()
    response = await add(client)

    assert listing["enabled"] is False
    assert response.status_code == 503


async def test_limit_is_enforced(
    client: httpx.AsyncClient, logged_in_user: User, payload_key: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(experiences, "MAX_EXPERIENCES", 1)
    assert (await add(client)).status_code == 201

    response = await add(client, title="두 번째")

    assert response.status_code == 409


async def test_too_long_body_is_rejected(
    client: httpx.AsyncClient, logged_in_user: User, payload_key: Settings
) -> None:
    response = await add(client, body="가" * (experiences.MAX_BODY_CHARS + 1))

    assert response.status_code == 422


async def test_delete_and_other_users_are_404(
    client: httpx.AsyncClient, make_user: UserFactory, logged_in_user: User, payload_key: Settings
) -> None:
    mine = (await add(client)).json()["id"]
    await make_user("other@example.com", "password-other")
    await client.post("/api/auth/logout")
    await login(client, "other@example.com", "password-other")

    assert (await client.get(f"/api/experiences/{mine}")).status_code == 404
    assert (await client.delete(f"/api/experiences/{mine}")).status_code == 404
    assert (await client.get("/api/experiences")).json()["experiences"] == []


async def test_delete_removes_row(client: httpx.AsyncClient, logged_in_user: User, payload_key: Settings) -> None:
    mine = (await add(client)).json()["id"]

    deleted = await client.delete(f"/api/experiences/{mine}")

    assert deleted.status_code == 204
    assert (await client.get(f"/api/experiences/{mine}")).status_code == 404
