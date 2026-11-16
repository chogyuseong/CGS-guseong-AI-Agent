"""웹 푸시 구독 API. 알림을 보내는 쪽은 app/push.py(notify).

화면의 '알림 켜기' 흐름:
    GET  /api/push/public-key   → {enabled, public_key}
    브라우저: Notification.requestPermission()
            → registration.pushManager.subscribe({userVisibleOnly: true, applicationServerKey: public_key})
    POST /api/push/subscribe    ← subscription.toJSON()   (204)
'알림 끄기':
    POST /api/push/unsubscribe  ← {endpoint}              (204, 없거나 남의 것이어도 204)

- 모두 로그인 필요. 상태를 바꾸는 요청은 Origin 검사(미들웨어)를 거친다.
- endpoint는 브라우저 푸시 서비스의 https 주소만 받는다(schemas/push.py, 서버가 아무 주소로나 요청을 보내지 않게).
- 같은 endpoint(같은 브라우저)가 다시 오면 키와 사용자를 갱신한다(다른 계정으로 로그인한 기기는 지금 사용자에게 간다).
"""

from fastapi import APIRouter, Request, Response, status
from sqlalchemy import delete, select

from app.config import Settings
from app.db import DbSession
from app.models import PushSubscription
from app.push import push_enabled
from app.schemas.push import PushConfigOut, PushSubscribeRequest, PushUnsubscribeRequest
from app.security import CurrentUser

router = APIRouter(prefix="/push", tags=["push"])

USER_AGENT_MAX = 500


@router.get("/public-key")
async def push_config(request: Request, user: CurrentUser) -> PushConfigOut:
    settings: Settings = request.app.state.settings
    return PushConfigOut(enabled=push_enabled(settings), public_key=settings.vapid_public_key or None)


@router.post("/subscribe", status_code=status.HTTP_204_NO_CONTENT)
async def subscribe(body: PushSubscribeRequest, request: Request, user: CurrentUser, session: DbSession) -> Response:
    row = await session.scalar(select(PushSubscription).where(PushSubscription.endpoint == body.endpoint))
    if row is None:
        row = PushSubscription(endpoint=body.endpoint, user_id=user.id, p256dh="", auth="")
        session.add(row)
    row.user_id = user.id
    row.p256dh = body.keys.p256dh
    row.auth = body.keys.auth
    row.user_agent = (request.headers.get("user-agent") or "")[:USER_AGENT_MAX] or None
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/unsubscribe", status_code=status.HTTP_204_NO_CONTENT)
async def unsubscribe(body: PushUnsubscribeRequest, user: CurrentUser, session: DbSession) -> Response:
    await session.execute(
        delete(PushSubscription).where(PushSubscription.endpoint == body.endpoint, PushSubscription.user_id == user.id)
    )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
