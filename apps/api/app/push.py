"""웹 푸시 알림. 밖에서는 PushNotifier.notify(user_id, kind, url) 하나만 쓴다(명세).

본문 규칙 (docs/SAFETY.md):
- 본문은 kind별 고정 문구뿐이다. 요청 내용, 도구 결과, 일정 제목, 메일 내용, 개인정보는 넣지 않는다.
  notify는 문자열을 받을 자리가 url 하나뿐이고, url은 화면 경로("/chat/7")다.
- 푸시 서버(FCM, Apple 등)는 암호화된 본문만 보지만, 그래도 기기 잠금 화면에 뜨는 글이라 고정 문구로 둔다.
- VAPID_SUBJECT(또는 키)가 비어 있으면 아무것도 보내지 않고 조용히 건너뛴다.
- 발송 실패는 작업을 실패시키지 않는다(예외를 밖으로 내지 않는다).
  푸시 서버가 404·410(구독 만료)을 주면 그 구독을 지운다.
- 로그에는 구독 id만 남긴다. endpoint(URL 안에 기기 토큰이 있다)와 키는 남기지 않는다.

흐름:
    notify(user_id, "approval_needed", "/chat/7")
      → 그 사용자의 push_subscriptions 전부에 {"title", "body", "url", "kind"} JSON을 암호화해 보낸다
        (pywebpush, VAPID 서명)
      → 성공한 구독은 last_used_at 갱신, 404·410인 구독은 삭제
"""

import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pywebpush import WebPushException, webpush_async
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.models import PushSubscription

logger = logging.getLogger(__name__)


class PushKind(StrEnum):
    APPROVAL_NEEDED = "approval_needed"
    TASK_DONE = "task_done"
    TASK_FAILED = "task_failed"
    TASK_NEEDS_REVIEW = "task_needs_review"  # 3단계 보강: 외부 작업 결과를 확인하지 못함
    WATCH_MET = "watch_met"  # 6단계
    BRIEFING = "briefing"  # 6단계


PUSH_TITLE = "마이에이전트"
# 명세의 고정 문구. 이 표 밖의 문자열은 본문에 들어가지 않는다.
PUSH_BODIES: dict[PushKind, str] = {
    PushKind.APPROVAL_NEEDED: "승인이 필요한 작업이 있습니다",
    PushKind.TASK_DONE: "맡기신 작업이 끝났습니다",
    PushKind.TASK_FAILED: "작업을 끝내지 못했습니다",
    PushKind.TASK_NEEDS_REVIEW: "처리 결과 확인이 필요합니다",
    PushKind.WATCH_MET: "지켜보던 조건이 충족되었습니다",
    PushKind.BRIEFING: "오늘의 브리핑이 준비되었습니다",
}
# 기기가 꺼져 있을 때 푸시 서버가 알림을 보관하는 시간(초). 승인은 30분 뒤 만료되므로 1시간이면 충분하다.
PUSH_TTL_SECONDS = 3600
PUSH_TIMEOUT_SECONDS = 10
GONE_STATUSES = {404, 410}


class PushGoneError(Exception):
    """구독이 만료되었거나 해지되었다(푸시 서버 404·410). 이 구독은 지운다."""


@dataclass(frozen=True)
class VapidKeys:
    private_key: str
    subject: str


# (구독 정보, 본문 JSON, VAPID) → 보낸다. 만료면 PushGoneError, 그 밖의 실패는 아무 예외.
type PushSender = Callable[[dict[str, Any], str, VapidKeys], Awaitable[None]]


async def send_with_pywebpush(subscription: dict[str, Any], payload: str, vapid: VapidKeys) -> None:
    """실제 푸시 서버로 보낸다(pywebpush). 테스트는 이 함수 대신 가짜 전송기를 쓴다."""
    try:
        await webpush_async(
            subscription,
            data=payload,
            vapid_private_key=vapid.private_key,
            vapid_claims={"sub": vapid.subject},  # 매번 새 dict: pywebpush가 aud·exp를 써 넣는다
            timeout=PUSH_TIMEOUT_SECONDS,
            ttl=PUSH_TTL_SECONDS,
        )
    except WebPushException as error:
        if error.status_code in GONE_STATUSES:
            raise PushGoneError from None
        raise RuntimeError(f"푸시 서버 응답 {error.status_code}") from None


def build_payload(kind: PushKind, url: str) -> str:
    """서비스 워커(public/sw.js)가 받는 JSON. 본문은 고정 문구다."""
    return json.dumps({"title": PUSH_TITLE, "body": PUSH_BODIES[kind], "url": url, "kind": kind.value})


def subscription_info(row: PushSubscription) -> dict[str, Any]:
    return {"endpoint": row.endpoint, "keys": {"p256dh": row.p256dh, "auth": row.auth}}


def push_enabled(settings: Settings) -> bool:
    """VAPID 세 값이 모두 있어야 보낸다. VAPID_SUBJECT가 비어 있으면 꺼진 것으로 본다."""
    return bool(settings.vapid_subject and settings.vapid_public_key and settings.vapid_private_key.get_secret_value())


@dataclass
class PushNotifier:
    session_factory: async_sessionmaker[AsyncSession]
    settings: Settings
    sender: PushSender = field(default=send_with_pywebpush)

    async def notify(self, user_id: int, kind: PushKind, url: str) -> int:
        """사용자의 모든 구독에 고정 문구 푸시를 보내고 성공한 수를 돌려준다. 예외를 밖으로 내지 않는다."""
        if not push_enabled(self.settings):
            return 0
        try:
            return await self.send_to_all(user_id, build_payload(kind, url))
        except Exception:
            logger.warning("푸시 발송 중 오류(작업에는 영향 없음): user=%s kind=%s", user_id, kind, exc_info=True)
            return 0

    async def send_to_all(self, user_id: int, payload: str) -> int:
        async with self.session_factory() as session:
            rows = list(await session.scalars(select(PushSubscription).where(PushSubscription.user_id == user_id)))
        vapid = VapidKeys(self.settings.vapid_private_key.get_secret_value(), self.settings.vapid_subject)
        sent, gone = [], []
        for row in rows:
            try:
                await self.sender(subscription_info(row), payload, vapid)
                sent.append(row.id)
            except PushGoneError:
                gone.append(row.id)
            except Exception as error:
                logger.warning("푸시 발송 실패: subscription=%s (%s)", row.id, type(error).__name__)
        await self.record(sent, gone)
        return len(sent)

    async def record(self, sent: list[int], gone: list[int]) -> None:
        async with self.session_factory() as session:
            if sent:
                await session.execute(
                    update(PushSubscription).where(PushSubscription.id.in_(sent)).values(last_used_at=datetime.now(UTC))
                )
            if gone:
                await session.execute(delete(PushSubscription).where(PushSubscription.id.in_(gone)))
            await session.commit()
