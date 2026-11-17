"""지금 시각 한 곳(8단계 보강).

모델이 받는 날짜 지시문(prompts), 날씨 예보 범위(weather), 자동화 생성 시각(tools/automation),
외부 콘텐츠 조회 시각(results)이 이 모듈의 now_utc()를 부른다. eval은 사례의 고정 시각으로 now_utc 하나만
바꿔 끼운다(evals/harness.py frozen_clock). 그래서 부르는 쪽은 `from app.clock import now_utc`가 아니라
`clock.now_utc()`로 부른다(바꿔 낀 함수가 보이게).

승인 만료·예산·세션처럼 실제 시간이 지나야 하는 검사는 이 모듈을 쓰지 않는다.
"""

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

SEOUL = ZoneInfo("Asia/Seoul")


def now_utc() -> datetime:
    return datetime.now(UTC)


def today_seoul() -> date:
    return now_utc().astimezone(SEOUL).date()
