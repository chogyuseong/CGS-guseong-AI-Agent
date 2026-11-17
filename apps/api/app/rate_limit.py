"""메시지 전송 제한: 사용자당 분당 20회 (Redis 없이, 프로세스 메모리에서).

슬라이딩 윈도: 사용자마다 최근 요청 시각을 deque에 담아 두고, 창(60초)보다 오래된 시각은 버린다.
남은 개수가 한도 이상이면 거절한다.
- 명세상 API 서버 프로세스는 하나라서 메모리 저장으로 충분하다. 서버를 재시작하면 기록이 비워진다.
- 프로세스를 여러 개로 늘리면 프로세스마다 따로 세게 되므로, 그때는 DB나 Redis로 옮겨야 한다.
"""

import time
from collections import defaultdict, deque
from collections.abc import Callable, Hashable

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
        self._hits[key].append(self._clock())
