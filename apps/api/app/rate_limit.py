"""메시지 전송 제한: 사용자당 분당 20회 (Redis 없이, 프로세스 메모리에서).

슬라이딩 윈도: 사용자마다 최근 요청 시각을 deque에 담아 두고, 창(60초)보다 오래된 시각은 버린다.
남은 개수가 한도 이상이면 거절한다.
- 명세상 API 서버 프로세스는 하나라서 메모리 저장으로 충분하다. 서버를 재시작하면 기록이 비워진다.
- 프로세스를 여러 개로 늘리면 프로세스마다 따로 세게 되므로, 그때는 DB나 Redis로 옮겨야 한다.

쓰는 곳(create_app이 app.state에 하나씩 만든다):
- message_rate_limiter: 메시지 전송(app/routers/conversations.py). 모델 호출 비용이 폭주하는 것을 막는다.
- login_rate_limiter: 로그인 실패(app/routers/auth.py build_login_rate_limiter, 이메일 키, 분당 5회).
  비밀번호 무작위 대입을 막는다. 실패만 기록(record)하고 시도 전에 blocked로 확인한다.
관련 테스트: tests/test_chat_api.py(창·한도), tests/test_auth.py(로그인 429).
clock은 테스트가 시각을 넘기려고 바꿔 끼운다(time.monotonic: 시스템 시계를 바꿔도 거꾸로 가지 않는다).
"""

import time
from collections import defaultdict, deque
from collections.abc import Callable, Hashable

# 사용자당 분당 메시지 수. 출처: 예전 명세(루트 SPEC.md "메시지 전송은 분당 20회로 제한").
# 바꾸면 tests/test_chat_api.py의 기본값 테스트도 같이 본다.
MESSAGES_PER_MINUTE = 20


class SlidingWindowRateLimiter:
    """키별 슬라이딩 윈도. 메시지는 user_id(int), 로그인은 이메일(str)을 키로 쓴다."""

    def __init__(
        self,
        limit: int = MESSAGES_PER_MINUTE,
        window_seconds: float = 60,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self._clock = clock
        self._hits: defaultdict[Hashable, deque[float]] = defaultdict(deque)

    def allow(self, key: Hashable) -> bool:
        """이번 요청을 허용하면 기록하고 True, 한도를 넘으면 기록하지 않고 False."""
        if self.blocked(key):
            return False
        self.record(key)
        return True

    def blocked(self, key: Hashable) -> bool:
        """창 안의 기록이 한도에 닿았는지만 본다(기록하지 않는다). 로그인은 실패만 기록하므로 따로 쓴다."""
        now = self._clock()
        hits = self._hits[key]
        while hits and now - hits[0] >= self.window_seconds:
            hits.popleft()
        return len(hits) >= self.limit

    def record(self, key: Hashable) -> None:
        """시각 하나를 기록한다(한도 검사 없음). 로그인 라우터는 비밀번호가 틀렸을 때만 부른다."""
        self._hits[key].append(self._clock())
