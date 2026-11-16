"""웹 푸시 구독 요청·응답. 화면은 브라우저의 PushSubscription.toJSON()을 그대로 보낸다."""

from typing import Annotated
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator

# 브라우저 푸시 서비스의 호스트(이 이름이거나 이 이름으로 끝나는 하위 도메인만 허용).
# 서버는 이 endpoint로 직접 HTTP 요청을 보내므로, 아무 주소나 받으면 내부망 주소로 요청을 보내게 만들 수 있다.
# - Chrome·Edge(안드로이드 포함): fcm.googleapis.com / Firefox: updates.push.services.mozilla.com
# - Safari·아이폰 홈 화면 앱: web.push.apple.com / 옛 Edge: *.notify.windows.com
ALLOWED_PUSH_HOSTS = ("googleapis.com", "push.services.mozilla.com", "push.apple.com", "notify.windows.com")

Base64Url = Annotated[str, Field(min_length=1, pattern=r"^[A-Za-z0-9_-]+={0,2}$")]


def is_push_service_url(url: str) -> bool:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not host:
        return False
    return any(host == allowed or host.endswith("." + allowed) for allowed in ALLOWED_PUSH_HOSTS)


class PushKeys(BaseModel):
    # 브라우저가 만든 수신용 공개 키(P-256)와 인증 비밀. 본문 암호화에 쓴다.
    p256dh: Annotated[Base64Url, Field(max_length=200)]
    auth: Annotated[Base64Url, Field(max_length=100)]


class PushEndpoint(BaseModel):
    endpoint: str = Field(min_length=1, max_length=2048)

    @field_validator("endpoint")
    @classmethod
    def must_be_push_service(cls, value: str) -> str:
        if not is_push_service_url(value):
            raise ValueError("브라우저 푸시 서비스의 https 주소가 아닙니다.")
        return value


class PushSubscribeRequest(PushEndpoint):
    """POST /api/push/subscribe 본문. PushSubscription.toJSON() 모양(expirationTime은 받기만 하고 쓰지 않는다)."""

    keys: PushKeys
    expirationTime: int | None = None  # noqa: N815 (브라우저가 보내는 이름 그대로)


class PushUnsubscribeRequest(PushEndpoint):
    """POST /api/push/unsubscribe 본문."""


class PushConfigOut(BaseModel):
    """GET /api/push/public-key 응답."""

    # 서버가 푸시를 보낼 수 있는가(VAPID 공개 키·개인 키·VAPID_SUBJECT가 모두 설정됨).
    # false면 화면은 '알림 켜기'를 막는다.
    enabled: bool
    # pushManager.subscribe({applicationServerKey})에 넣을 VAPID 공개 키(base64url). 설정이 없으면 null.
    public_key: str | None
