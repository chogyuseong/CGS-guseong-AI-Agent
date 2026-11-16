"""6단계 자동화: 예약 작업(scheduled_jobs), 조건 감시(watches), 아침 브리핑, 1분 검사.

- rules.py   제한 값과 시각 계산(DB 없이 계산만)
- store.py   예약·감시를 만들고 켜고 끄고 지우는 DB 작업(서버 제한 강제). 도구와 화면 API가 같이 쓴다
- watchable.py 감시에 쓸 수 있는 도구(READ, 메일 제외)와 인자 검사, 감시 실행 때 도구 부르기
- watch.py   감시 판정 에이전트(WatchVerdict)
- check.py   1분 검사(run_check), 예약 실행(run_scheduled_job), 감시 실행(run_watch)
"""
