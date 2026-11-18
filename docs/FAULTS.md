# 장애 주입 결과 (3단계 보강)

BUILD_SPEC 13절 3단계 "장애 지점별 외부 효과와 DB 상태 표". 외부 작업 하나(fake_external, 가짜 외부 서비스)를
승인한 뒤 각 지점에서 프로세스가 죽었을 때 남는 상태를 만들고, 재개 워크플로를 실제로 돌려 확인했다.

- 실행: 2026-11-17, 두 번째 PC, `uv run python -m pytest tests/test_faults.py tests/test_fake_service.py`
- 가짜 외부 서비스는 SQLite 파일에 기록한다(tests/fake_service.py). 앱·워크플로를 다시 만들어도 기록이 남는다.
- "프로세스 종료"는 그 지점에서 남는 DB·외부 상태를 만들어 재현했다. 실제 프로세스를 강제 종료하는 절차는
  docs/DURABILITY-CHECK.md(워크플로 복구)에 있고, 이번 단계에서는 다시 하지 않았다.

| 지점 | 죽은 직후 상태 | 복구 동작 | 외부 효과 | 복구 뒤 DB | 테스트 |
|---|---|---|---|---|---|
| 1. 외부 요청 전 | operation executing, step running, 외부 기록 없음 | 다시 보내지 않고 operation_id로 재조회 → 404 | 0건 | operation failed(not_executed), step failed, task done | test_faults::test_crash_before_request_is_confirmed_not_executed |
| 2. 외부 처리 후 응답 전 | operation executing, 외부 기록 있음 | 재조회 → 처리 기록 있음 → 영수증 생성 | 1건 | operation succeeded + 영수증, step done, task done | test_faults::test_crash_after_external_success_reconciles_to_receipt |
| 2'. 응답 유실(프로세스는 삶) | 쓰기 요청 시간 초과 → 결과 unknown | 실패로 단정하지 않고 바로 재조회 | 1건 | operation succeeded + 영수증 | test_faults::test_lost_response_without_crash_is_reconciled_at_once |
| 3. 성공 응답 후 영수증 저장 전 | 2와 같음(영수증 없음) | 2와 같음 | 1건 | 2와 같음 | (2와 같은 테스트) |
| 4. 영수증 저장 후 워크플로 완료 전 | operation succeeded + 영수증, step running | 외부를 부르지 않고 저장된 영수증 결과를 돌려줌 | 1건 | step done(receipt_id), task done | test_faults::test_crash_after_receipt_reuses_receipt |
| 5. 승인 커밋 후 재개 등록 전 | 승인 approved, 재개 워크플로 없음 | 서버 시작 때 restart_orphaned_tasks가 재개(두 번 해도 같은 워크플로 ID) | 1건 | operation succeeded, task done | test_fake_service::test_resume_recovered_after_crash_before_resume_registration |
| 재조회도 실패 | operation executing, 조회 오류 | 단정하지 않음 | 모름 | operation unknown, step unknown, task needs_review(푸시 task_needs_review) | test_faults::test_failed_lookup_keeps_unknown_and_needs_review |

보장 범위(BUILD_SPEC 4.1):
- 완료 기록이 있는 DBOS 단계(모델 응답, finish, notify)는 복구 때 결과를 재사용한다.
- 도구 실행은 DBOS 단계가 아니다. 끊긴 EXTERNAL 호출은 다시 보내지 않고 operation_id(외부 멱등성 키)로 결과를 묻는다.
- 결과를 확인할 수 없으면 unknown으로 두고 사용자에게 확인을 요청한다. 새 operation으로 자동 재시도하지 않는다.
- DBOS만으로 외부 API exactly-once를 주장하지 않는다. 중복 방지는 외부 서비스의 멱등성 키와 결과 조회에 기댄다.
- (3단계 당시) Google 일정 도구는 재조회 함수가 없어 끊긴 일정 쓰기가 unknown·needs_review로 남았다.
  4단계 보강부터 `gcal.reconcile_create`·`reconcile_update`·`reconcile_delete`가 도구 규격(`SPECS`의 `reconcile=`)에 있어
  위 표와 같은 흐름으로 재조회한다(고정 event id·ETag로 결과 확인). 2026-11-18 문서 갱신.

## 로그·기록 민감 정보 (3단계 보강)

가짜 비밀 문장을 사용자 메시지·외부 일정 제목·외부 오류 문장에 넣고 장애 경로(outbox 시작 실패, 푸시 실패, 재조회 실패,
외부 쓰기 시간 초과)를 거친 뒤 확인했다(tests/test_sensitive_records.py).

| 저장소 | 결과 |
|---|---|
| 앱 로그(app.*, traceback 포함) | 처음에는 재조회 실패 로그에 외부 오류 문장이 그대로 남았다 → 예외 메시지 가리기(install_log_redaction) 뒤 없음 |
| outbox_events | 없음(오류는 종류 이름만, 사용자 메시지는 복사하지 않음) |
| notifications | 없음(종류·경로·결과·기기 수만) |
| operations.error_code | 없음(정해진 코드만) |
| messages, message_history, steps, execution_receipts | 내용이 남는다(화면·모델에 필요). 보관·삭제는 5단계 |
| DBOS 시스템 테이블 | 워크플로 입력·단계 결과에 사용자 글이 남는다. 5·7단계에서 다룬다 |
