"""테스트 전용 가짜 푸시 전송기. 실제 푸시 서버(FCM, Apple 등)로는 아무것도 보내지 않는다.

보낸 구독과 본문(JSON 문자열)을 기록해 두고, endpoint별로 실패(만료 404·410, 그 밖의 오류)를 흉내 낸다.
"""

import json
from dataclasses import dataclass, field
from typing import Any

from app.push import PushGoneError, VapidKeys


@dataclass
class FakePushSender:
    sent: list[tuple[str, str]] = field(default_factory=list)  # (endpoint, payload)
    failures: dict[str, Exception] = field(default_factory=dict)  # endpoint → 던질 예외

    async def __call__(self, subscription: dict[str, Any], payload: str, vapid: VapidKeys) -> None:
        endpoint = subscription["endpoint"]
        if endpoint in self.failures:
            raise self.failures[endpoint]
        self.sent.append((endpoint, payload))

    def payloads(self) -> list[dict[str, Any]]:
        return [json.loads(payload) for _, payload in self.sent]

    def expire(self, endpoint: str) -> None:
        self.failures[endpoint] = PushGoneError()
