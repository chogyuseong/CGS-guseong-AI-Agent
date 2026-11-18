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
- 근거: DECISIONS 2026-11-17(구독 API 3개, enabled는 VAPID 세 값이 모두 있을 때만, endpoint 허용 호스트로 SSRF 방지,
  푸시 본문은 고정 문구라 요청 내용이 들어가지 않음).

고칠 때 주의:
- 경로·응답 모양을 바꾸면 gen:api 후 apps/web/lib/push.ts(public-key·subscribe·unsubscribe)를 고친다.
- 허용 호스트 목록은 app/schemas/push.py의 ALLOWED_PUSH_HOSTS다. 보내는 쪽(404·410이면 구독 삭제)은 app/push.py.
- 관련 테스트: tests/test_push.py, test_openapi.py.
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

# 구독 행에 남기는 User-Agent 길이 상한. 헤더가 길어도 이 길이까지만 저장해 행 크기를 묶는다.
USER_AGENT_MAX = 500


# VAPID 공개 키와 푸시 가능 여부. 공개 키라 내보내도 된다(개인 키는 절대 응답에 넣지 않는다). 로그인 필요.
@router.get("/public-key")
async def push_config(request: Request, user: CurrentUser) -> PushConfigOut:
    settings: Settings = request.app.state.settings
    return PushConfigOut(enabled=push_enabled(settings), public_key=settings.vapid_public_key or None)


# 구독 저장(커밋). endpoint 검사는 스키마가 한다(허용 호스트가 아니면 422).
# endpoint로 찾을 때 user_id 조건을 걸지 않는 것은 의도다: endpoint는 브라우저 하나를 뜻하므로, 그 브라우저에서
# 지금 로그인한 사용자에게 옮긴다(앞 사용자의 알림이 이 기기로 계속 오지 않게).
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


# 구독 삭제(커밋). 내 구독만 지운다(user_id 조건). 없거나 남의 것이어도 204라 남의 구독 존재를 드러내지 않는다.
@router.post("/unsubscribe", status_code=status.HTTP_204_NO_CONTENT)
async def unsubscribe(body: PushUnsubscribeRequest, user: CurrentUser, session: DbSession) -> Response:
    await session.execute(
        delete(PushSubscription).where(PushSubscription.endpoint == body.endpoint, PushSubscription.user_id == user.id)
    )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
