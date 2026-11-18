"""지금 시각 한 곳(8단계 보강).

모델이 받는 날짜 지시문(prompts), 날씨 예보 범위(weather), 자동화 생성 시각(tools/automation),
외부 콘텐츠 조회 시각(results)이 이 모듈의 now_utc()를 부른다. eval은 사례의 고정 시각으로 now_utc 하나만
바꿔 끼운다(evals/harness.py frozen_clock). 그래서 부르는 쪽은 `from app.clock import now_utc`가 아니라
`clock.now_utc()`로 부른다(바꿔 낀 함수가 보이게).

승인 만료·예산·세션처럼 실제 시간이 지나야 하는 검사는 이 모듈을 쓰지 않는다.

근거: docs/DECISIONS.md 2026-11-17 "eval의 '지금'은 app/clock.py 한 곳에서 읽게 바꿨다".

고칠 때 주의
- 새로 "오늘 날짜"를 모델에게 보여 주거나 날짜 범위를 계산하는 곳은 datetime.now 대신 clock.now_utc()를 쓴다.
  그러지 않으면 eval 사례가 고정 시각과 실제 시각이 섞인 채 돌아 결과가 날마다 달라진다.
- today_seoul도 모듈 전역 now_utc를 거쳐 부르므로 frozen_clock(now_utc만 바꿈)이 같이 적용된다.
  today_seoul 안에서 datetime.now를 직접 부르게 바꾸면 eval의 날짜가 풀린다.
- 관련: evals/harness.py(frozen_clock), evals/record.py.
"""

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

# 사용자가 한국에 있다는 전제(날짜 경계·"오늘"은 서울 기준). 예산(agent/budget.py)·비용 화면도 서울 0시를
# 경계로 쓰지만 이 상수가 아니라 실제 시각으로 따로 계산한다(eval 고정 시각을 따르지 않게).
SEOUL = ZoneInfo("Asia/Seoul")


def now_utc() -> datetime:
    """지금 시각(UTC, 시간대 있음). eval이 이 함수 하나를 바꿔 끼워 세상을 고정한다."""
    return datetime.now(UTC)


def today_seoul() -> date:
    """서울 기준 오늘 날짜. now_utc를 거치므로 eval의 고정 시각을 따른다."""
    return now_utc().astimezone(SEOUL).date()
