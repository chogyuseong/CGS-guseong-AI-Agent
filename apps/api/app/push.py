"""웹 푸시 알림. 밖에서는 PushNotifier.notify(user_id, kind, internal_url, notification_key) 하나만 쓴다(명세 8.4).

본문 규칙 (docs/SAFETY.md):
- 본문은 kind별 고정 문구뿐이다. 요청 내용, 도구 결과, 일정 제목, 메일 내용, 개인정보는 넣지 않는다.
  notify는 문자열을 받을 자리가 url 하나뿐이고, url은 화면 경로("/chat/7")다.
- 푸시 서버(FCM, Apple 등)는 암호화된 본문만 보지만, 그래도 기기 잠금 화면에 뜨는 글이라 고정 문구로 둔다.
- VAPID_SUBJECT(또는 키)가 비어 있으면 아무것도 보내지 않고 조용히 건너뛴다.
- 발송 실패는 작업을 실패시키지 않는다(예외를 밖으로 내지 않는다).
  푸시 서버가 404·410(구독 만료)을 주면 그 구독을 지운다.
- 로그에는 구독 id만 남긴다. endpoint(URL 안에 기기 토큰이 있다)와 키는 남기지 않는다.
- 3단계 보강: 같은 (사용자, notification_key)는 한 번만 보낸다(notifications 테이블, 결과·기기 수 기록).
  url은 앱 내부 경로만("/…"). 전달을 정확히 한 번 보장한다고 주장하지 않는다(푸시 서버·기기 사정).

흐름:
    notify(user_id, "approval_needed", "/chat/7")
      → 그 사용자의 push_subscriptions 전부에 {"title", "body", "url", "kind"} JSON을 암호화해 보낸다
        (pywebpush, VAPID 서명)
      → 성공한 구독은 last_used_at 갱신, 404·410인 구독은 삭제

진입점: 에이전트 실행(runner의 notify 단계)과 자동화가 RunServices.notifier로 notify를 부른다. 관리 명령
send-test-push(app/cli.py)도 같은 notify를 쓴다. 실제 전송기는 app.state.push_sender(create_app이 send_with_pywebpush).
근거: docs/DECISIONS.md 2026-11-17(본문 JSON 모양, TTL 1시간, 실패는 경고만, pywebpush 2.5.0).

고칠 때 주의
- 문구(PUSH_BODIES)나 kind를 바꾸면 웹 서비스 워커(apps/web/public/sw.js)·docs/PUSH-CHECK.md·cli의 --kind 선택지가
  같이 바뀐다. 본문에 요청 내용·도구 결과를 넣는 인자를 새로 만들지 않는다(잠금 화면 노출, docs/SAFETY.md).
- VAPID 키를 바꾸면 기존 구독이 맞지 않는다(사용자가 알림을 다시 켜야 함, docs/OPERATIONS.md 4절).
- 관련 테스트: tests/test_push.py, tests/test_notifications.py(중복 방지), tests/fake_push.py(가짜 전송기).
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
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.models import Notification, PushSubscription
from app.models.enums import NotificationStatus

logger = logging.getLogger(__name__)


class PushKind(StrEnum):
    """알림 종류. 값은 본문 JSON의 kind와 notifications.kind에 그대로 들어간다(값을 바꾸면 옛 기록과 달라진다)."""

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
    PushKind.BRIEFING: "브리핑이 준비되었습니다",
}
# 기기가 꺼져 있을 때 푸시 서버가 알림을 보관하는 시간(초). 승인은 30분 뒤 만료되므로 1시간이면 충분하다.
PUSH_TTL_SECONDS = 3600
# 푸시 서버 한 곳에 보내는 요청의 제한 시간(초). 기기마다 차례로 보내므로 느린 서버 하나가 작업 끝을 오래 막지 않게.
PUSH_TIMEOUT_SECONDS = 10
# Web Push 규약에서 구독이 더는 없다는 응답. 이때만 구독을 지운다(그 밖의 실패는 다음에 다시 보낸다).
GONE_STATUSES = {404, 410}
# 화면 경로 길이 상한. 내부 경로("/chat/7")는 짧으므로, 길면 잘못 만든 주소로 보고 보내지 않는다.
MAX_URL_LENGTH = 200
# notifications.notification_key 칸 길이(String(200))와 같다. 넘으면 잘라서 쓴다(같은 앞부분이면 한 번만 보낸다).
MAX_KEY_LENGTH = 200


class PushGoneError(Exception):
    """구독이 만료되었거나 해지되었다(푸시 서버 404·410). 이 구독은 지운다."""


@dataclass(frozen=True)
class VapidKeys:
    """전송 서명에 쓰는 VAPID 개인 키와 subject(연락처). 공개 키는 브라우저 구독 때 화면이 쓴다."""

    private_key: str
    subject: str


# (구독 정보, 본문 JSON, VAPID) → 보낸다. 만료면 PushGoneError, 그 밖의 실패는 아무 예외.
type PushSender = Callable[[dict[str, Any], str, VapidKeys], Awaitable[None]]


async def send_with_pywebpush(subscription: dict[str, Any], payload: str, vapid: VapidKeys) -> None:
    """실제 푸시 서버로 보낸다(pywebpush, 외부 호출). 테스트는 이 함수 대신 가짜 전송기를 쓴다.

    만료(404·410)면 PushGoneError, 그 밖의 실패는 상태 코드만 담은 RuntimeError. 원래 예외는 끊는다(from None):
    pywebpush 예외에는 endpoint(기기 토큰이 든 URL)와 응답 본문이 들어 있어 로그로 새는 것을 막는다.
    """
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
    """DB 행을 pywebpush가 받는 구독 정보 모양(브라우저 PushSubscription.toJSON과 같음)으로."""
    return {"endpoint": row.endpoint, "keys": {"p256dh": row.p256dh, "auth": row.auth}}


def push_enabled(settings: Settings) -> bool:
    """VAPID 세 값이 모두 있어야 보낸다. VAPID_SUBJECT가 비어 있으면 꺼진 것으로 본다."""
    return bool(settings.vapid_subject and settings.vapid_public_key and settings.vapid_private_key.get_secret_value())


def is_internal_url(url: str) -> bool:
    """앱 안의 경로("/chat/7")만 허용한다. 다른 사이트로 보내는 주소(https://…, //…, 역슬래시)는 거부한다.

    막는 사고: 알림을 누르면 피싱 사이트로 열리는 것. "//evil"과 "/\\evil"은 브라우저가 다른 호스트로 해석한다.
    서비스 워커(sw.js)도 같은 출처인지 다시 확인한다(두 겹).
    """
    return (
        url.startswith("/")
        and not url.startswith("//")
        and "\\" not in url
        and "://" not in url
        and len(url) <= MAX_URL_LENGTH
    )


@dataclass
class PushNotifier:
    session_factory: async_sessionmaker[AsyncSession]
    settings: Settings
    sender: PushSender = field(default=send_with_pywebpush)

    async def notify(self, user_id: int, kind: PushKind, internal_url: str, notification_key: str) -> int:
        """고정 문구 푸시를 사용자의 모든 구독에 보내고 성공한 기기 수를 돌려준다. 예외를 밖으로 내지 않는다.

        같은 (user, notification_key)는 한 번만 보낸다(복구로 같은 알림 단계가 다시 돌아도). 결과는 notifications에.
        순서: claim(행 먼저 커밋) → 전송 → finish(결과 기록). 행을 잡은 뒤 보내기 전에 죽으면 그 알림은 빠진다
        (정확히 한 번 전달을 주장하지 않는다. 결과는 앱 안 작업 목록으로 확인한다, DECISIONS 2026-11-17).
        키 형식은 부르는 쪽이 정한다(task 알림 `{종류}:task-{id}:{워크플로 ID}`, 감시 `watch_met:...`, CLI는 새 UUID).
        로그에는 user id와 kind만 남긴다(주소·본문·endpoint 없음).
        """
        if not is_internal_url(internal_url):
            logger.warning("앱 밖 주소라 푸시하지 않음: user=%s kind=%s", user_id, kind)
            return 0
        key = notification_key[:MAX_KEY_LENGTH]
        try:
            if not await self.claim(user_id, kind, internal_url, key):
                return 0
            sent, status = await self.deliver(user_id, kind, internal_url)
            await self.finish(user_id, key, status, sent)
            return sent
        except Exception:
            logger.warning("푸시 발송 중 오류(작업에는 영향 없음): user=%s kind=%s", user_id, kind, exc_info=True)
            return 0

    async def deliver(self, user_id: int, kind: PushKind, url: str) -> tuple[int, NotificationStatus]:
        """(보낸 기기 수, notifications에 남길 상태). 꺼짐(disabled)·구독 없음·전부 실패·보냄을 구분한다."""
        if not push_enabled(self.settings):
            return 0, NotificationStatus.DISABLED
        sent, total = await self.send_to_all(user_id, build_payload(kind, url))
        if sent:
            return sent, NotificationStatus.SENT
        return 0, NotificationStatus.FAILED if total else NotificationStatus.NO_SUBSCRIPTION

    async def claim(self, user_id: int, kind: PushKind, url: str, key: str) -> bool:
        """이 키의 기록을 만든다. 이미 있으면(보냈거나 보내는 중) False.

        INSERT ... ON CONFLICT DO NOTHING이라 동시에 두 번 불려도 한쪽만 True다(유니크 (user_id, notification_key)).
        자기 세션으로 바로 커밋한다.
        """
        async with self.session_factory() as session:
            created = await session.scalar(
                insert(Notification)
                .values(user_id=user_id, notification_key=key, kind=kind.value, url=url)
                .on_conflict_do_nothing(index_elements=["user_id", "notification_key"])
                .returning(Notification.id)
            )
            await session.commit()
        return created is not None

    async def finish(self, user_id: int, key: str, status: NotificationStatus, sent: int) -> None:
        """claim이 만든 행에 결과 상태와 보낸 기기 수를 적고 커밋한다."""
        async with self.session_factory() as session:
            await session.execute(
                update(Notification)
                .where(Notification.user_id == user_id, Notification.notification_key == key)
                .values(status=status, sent_count=sent)
            )
            await session.commit()

    async def send_to_all(self, user_id: int, payload: str) -> tuple[int, int]:
        """(성공한 기기 수, 구독 수). 기기 하나의 실패가 다른 기기 전송을 막지 않는다.

        구독 조회 세션은 전송 전에 닫는다(외부 호출 동안 DB 연결을 잡고 있지 않게). 실패 로그는 구독 id와
        예외 종류만(endpoint·키 없음).
        """
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
        return len(sent), len(rows)

    async def record(self, sent: list[int], gone: list[int]) -> None:
        """성공한 구독은 last_used_at 갱신, 만료된 구독은 삭제하고 한 번에 커밋한다."""
        async with self.session_factory() as session:
            if sent:
                await session.execute(
                    update(PushSubscription).where(PushSubscription.id.in_(sent)).values(last_used_at=datetime.now(UTC))
                )
            if gone:
                await session.execute(delete(PushSubscription).where(PushSubscription.id.in_(gone)))
            await session.commit()
