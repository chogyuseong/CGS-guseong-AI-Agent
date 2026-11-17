# 진행 상태

BUILD_SPEC 19절 형식. 단계가 끝날 때마다 고친다.

현재 단계: BUILD_SPEC 기준 6단계 보강 진행 중(2026-11-17). (1) 브리핑 사용자 동의, (2) 발생 기록, (3) 감시 판정 끝. 다음은 (4) 구조화된 제안.

완료한 항목:
- 예전 명세(SPEC.md) 기준 0~9단계 구현(배포 제외). 기능 목록은 README.md
- 0단계 보강(2026-11-17): 서버 저장형 세션, 로그인 실패 속도 제한, APP_ENV, 테스트 DB 보호 확대, .gitattributes,
  docs/BUILD_SPEC.md·STATUS.md·VERSIONS.md
- 1단계 보강(2026-11-17): client_request_id, SMART 전환 제거·호출 제한 시간, 호출별 예산 예약·정산,
  task 상태 12개·취소 API·agent_runs, ToolResult 구조(succeeded/failed/unknown)
- 2단계 보강(2026-11-17): 실행 계획(action_plans)·operations·execution_receipts, 인자 정규화·해시 고정,
  실행기 DB 검사 전부(executor.begin_execution), 저장된 인자로만 실행(override_args), 영수증 기준 완료,
  재시작과 무관한 가짜 외부 서비스(tests/fake_service.py), 승인 카드(대상·계정·실행 유효 시간·AI 검토 문구·실행 결과)
- 4단계 보강(2026-11-17): 일정 생성 고정 event id·409·재조회, 수정·삭제 ETag·재승인·참석자/반복 거부, OAuth POST·state DB·
  해제 시 계획 무효화·갱신 잠금, draft_message(초안 카드), 데이터 전송 등급·공개 검색 차단, 도구 체크리스트
- 3단계 보강(2026-11-17): 끊긴 외부 작업 결과 재조회(reconcile)·needs_review, 장애 지점 5곳 결과 표(docs/FAULTS.md),
  취소와 실행 경합, outbox(실행·재개 시작 요청), 푸시 notification_key·notifications, 앱 로그 예외 메시지 가리기

실제 검증한 항목(2026-11-17, 두 번째 PC, 4단계 보강 끝):
- API pytest 498개(`uv run python -m pytest`), ruff·pyright 0건, 새 마이그레이션 f4bbe5621c5e(oauth_states) 업·다운·업과 check 차이 없음
- 웹 Biome 0건, tsc, Vitest 277개, Playwright 37개(4단계 (3) 끝, 실제 API 서버 + Chrome 360px)
- 4단계 완료 증거(BUILD_SPEC 13절)와 해당 테스트(가짜 Google 응답):
  - 승인 후 테스트 일정 생성: test_gcal_create::test_create_sends_stable_id_and_records_it, test_calendar_flow
  - 승인 대기 중 대상 변경 시 재승인: test_gcal_change::test_stale_update_asks_for_new_approval
  - 타임아웃 후 결과 확인: test_gcal_create::test_timeout_then_found_is_success, test_timeout_then_missing_is_not_executed
  - 참석자 있는 일정 변경 거부: test_gcal_change::test_events_with_attendees_or_series_are_refused
  - 출처 없는 영업시간·가격을 만들지 않음: 시스템 프롬프트 규칙(test_data_policy::test_prompt_forbids_unsourced_place_details).
    실제 모델 행동은 8단계 eval에서 잰다(이번에는 실제 모델을 부르지 않음)
- 실제 Google 계정으로 확인: 하지 않음(Google 콘솔 설정·연결이 남아 있고, 실제 계정 변경은 사용자 확인 후에만)

미실행 항목과 이유:
- 실제 프로세스 강제 종료 복구: 이번에도 하지 않음. 장애 지점은 그 지점의 DB·외부 상태를 만들어 재개 워크플로를
  실제로 돌리는 방식으로 재현했다(docs/FAULTS.md). 1분 검사 워크플로 단계 구성이 바뀌어 WORKFLOW_VERSION을 myagent-4로
  올렸으므로, 원하면 docs/DURABILITY-CHECK.md 절차로 한 번 더 확인할 수 있다
- 웹 푸시 PC 확인(2026-11-17, 사용자): Chrome에서 알림 켜기 → send-test-push 3번 → 알림 표시 확인
- iOS·Android 실기기 푸시: 미확인. 이 PC의 네트워크가 터널을 막는다(cloudflared 7844 UDP·TCP 차단, localtunnel은 연결이
  자주 끊겨 503). 다른 네트워크(폰 핫스팟·집)나 9단계 배포 주소에서 확인한다(사용자 결정)
- HTTPS 개발 접속(폰): 터널 방법만 문서(docs/PUSH-CHECK.md). 실기기 확인은 사용자

사용자 확인이 필요한 항목:
- 이 PC의 .env는 Django판 형식이었다. DATABASE_URL(로컬 docker), SESSION_SECRET(새로 만듦), APP_ORIGIN을 넣고
  모델 이름에 `anthropic:`을 붙이고 TASK_MAX_STEPS→TASK_MAX_REQUESTS, GOOGLE_REDIRECT_URI를 새 주소로 바꿨다.
  VAPID_SUBJECT는 여전히 비어 있다
- 배포-이어하기.txt 2번의 남은 일(Google 콘솔, 푸시, eval, 배포처)

다음 시작 지점: 6단계 보강(아래 차이 목록의 6단계). 남긴 것: AgentReply(6단계로), 메일 실행 기록 암호화(7단계).
DBOS 워크플로 입력·단계 결과·message_history의 사용자 글은 5단계 보강에서 7일 정리·대화 삭제로 지운다(docs/DATA_POLICY.md)
관련 커밋: (이 파일을 커밋한 커밋)

---

## BUILD_SPEC과 지금 코드의 차이 (단계별 보강 목록)

2026-11-17에 BUILD_SPEC과 코드를 대조해 만든 목록이다. 단계를 보강할 때 이 목록부터 다시 확인한다.

### 1단계 — 작업 실행과 내부 도구
- [x] client_request_id로 메시지·task 중복 방지(사용자 범위 고유) — tests/test_client_request_id.py
- [x] agent_runs(최초 실행·재개 회차) 기록 — tests/test_cancel.py. (메시지 기록은 아직 tasks.message_history)
- [x] 예산: 모델 호출마다 예약 → 정산(budget_reservations, daily_budgets 잠금) — tests/test_budget_reservations.py
- [x] 가격 미등록 모델은 호출 차단
- [x] FAST→SMART 자동 전환 제거(BUILD_SPEC 6.1: 도구 호출 횟수만으로 전환 금지). `app/agent/model_choice.py`
- [x] task 상태 12개, 취소 API `POST /tasks/{id}/cancel` — tests/test_cancel.py
- [x] ToolResult를 status(succeeded/failed/unknown)·error_code·retryable·operation_id·receipt_id·truncated 구조로 — tests/test_tool_result.py
- [ ] (6단계로 옮김, 사용자 승인) AgentReply(text, sources, suggestions, needs_clarification) 구조화 출력 — 이유는 DECISIONS 1단계 보강
- [x] 내부 DB 변경(todo 등)의 중복 방지 확인 — test_client_request_id(재전송), test_workflows(복구 재생·끊긴 호출 미실행)
- [x] MODEL_CALL_TIMEOUT_SECONDS

### 2단계 — 승인 계획
- [x] action_plans(operation_id, canonical_args, args_hash, tool_version, connection_id, target_version, 만료 2종, policy_version) — tests/test_action_plans.py
  (target_version 칸만 있음. ETag 비교는 4단계. 실행 유효 시간은 approvals.execute_before, DECISIONS 2단계 보강)
- [x] approvals를 계획·승인 회차별로. 승인 후 실행 유효 시간(기본 5분) — 실행 때 검사는 (2)에서
- [x] operations, execution_receipts. 완료 표시는 영수증 기준 — tests/test_executor.py (화면 표시는 (4))
- [x] 실행기의 DB 검사 목록(3.3) 전부 — app/agent/executor.py (대상 버전은 4단계 ETag)
- [x] 재시작과 무관하게 기록이 남는 가짜 외부 서비스(멱등성 키·결과 조회) — tests/fake_service.py(SQLite 파일), tests/test_fake_service.py
- [x] 승인 카드: 계정·캘린더, 변경 전후, 유효 시간, 감시 배지 문구(일치로 추정/요청과 다를 수 있음/확인 필요) — tests/test_approval_card_api.py,
  components/ApprovalCard.test.tsx. 계정은 "연결된 Google 계정"까지(주소는 4단계 OAuth 보강 때 저장)

### 3단계 — 장애 주입·복구·푸시
- [x] 장애 지점 5곳 주입 테스트와 결과 표 — tests/test_faults.py, docs/FAULTS.md
- [x] unknown/reconciling, 결과 재조회, 성공 operation 영수증 재사용 — executor.reconcile_operation (Google 일정은 4단계)
- [x] outbox(task 저장↔워크플로 등록, 승인↔재개, 예약 발생↔task) 또는 DBOS 트랜잭션 연계 확인 — app/agent/outbox.py, tests/test_outbox.py
  (예약 발생↔task는 같은 워크플로 안이라 outbox 불필요)
- [x] 취소와 실행 경합 — tests/test_cancel_race.py
- [x] 푸시: notify(user_id, kind, internal_url, notification_key), notifications 테이블, 404·410 구독 정리, 짧은 채팅 완료엔 푸시 안 함 — tests/test_notifications.py, test_push.py
  (iOS·Android 실기기 확인은 사용자)

### 4단계 — 실제 조회와 캘린더
- [x] create_event: 안정적인 event ID, 중복 응답·타임아웃 구분 — tests/test_gcal_create.py
- [x] update/delete_event: ETag 조건부 요청, 대상 변경 시 stale → 재조회 → 재승인 — tests/test_gcal_change.py
- [x] 참석자 있는 일정 수정·삭제 거부, 반복 일정 시리즈 변경 제외 — tests/test_gcal_change.py
- [x] draft_message 도구(INTERNAL, 초안만, 복사 버튼) — tests/test_drafts.py, components/DraftCard.test.tsx
- [x] OAuth: 시작을 POST로, state를 DB에 세션·만료·단일 사용으로, 연결 해제 시 대기 중 계획 무효화 — tests/test_google_connection.py, e2e/connections.spec.ts
- [x] 도구 정의에 데이터 전송 등급(public/user_private/mail_private/secret)과 정책 — app/agent/data_policy.py, tests/test_data_policy.py
- [x] TOOL 규약을 BUILD_SPEC 17절 체크리스트로 — AGENTS.md 도구 규약

### 5단계 — 기억과 데이터 삭제
- [x] memories에 source_message_id, source_quote. 원문에 실제로 있는지 서버 검사 — tests/test_memory_quotes.py
- [x] "외부 도구를 썼으면 저장 금지"(MEMORY_BLOCKING_TOOLS) 제거(BUILD_SPEC 5.4가 금지)
- [x] 활성 기억 최대 200개, 한 줄 300자 — 이미 있었음(tests/test_memories.py)
- [x] 대화 삭제(DELETE /conversations/{id})와 관련 기록 정리 — tests/test_conversation_delete.py, e2e/conversation-delete.spec.ts
- [x] 실행 기록 보관 기간(EXECUTION_RETENTION_DAYS 7, RECEIPT_RETENTION_DAYS 90)과 정리 — app/automation/retention.py, tests/test_retention.py
- [x] docs/DATA_POLICY.md(저장소별 보관·삭제 범위, 백업 지연, 키 관리), 설정 화면 '데이터 보관' 안내
- 완료 증거: tests/test_memory_quotes.py(저장·새 대화 반영·삭제 뒤 제외·외부/인용/추론 거부), docs/DATA_POLICY.md
- 남긴 것: 메일 실행 기록 암호화(PAYLOAD_ENCRYPTION_KEY, 7단계), 계정 삭제 화면·API(요구사항에 없음, 제안), 배포 백업 30일 실제 설정(배포 단계)

### 6단계 — 예약과 감시
- [x] 계정 생성만으로 브리핑 예약을 만들지 않음. 사용자가 켠 뒤 실행 — PUT /api/schedules/briefing, tests/test_briefing_consent.py
- [x] automation_occurrences로 (job/watch, scheduled_for) 중복 방지, 누락은 유예 시간 안 1건만 — tests/test_occurrences.py
- [x] 예약 하루 1회 이하, weekdays·time_of_day·timezone(scheduled_jobs.timezone, 기본 Asia/Seoul)
- [x] 감시: 숫자 조건 서버 비교(LLM 없음), 판정 오류는 unknown, 재알림 최소 12시간, 단발성 날짜 고정 — tests/test_watch_conditions.py
- [ ] 제안은 구조화된 배열(suggestions: title, proposed_user_message, reason). 지금은 `[제안]` 문자열 파싱
- [ ] 예산 부족 시 paused_budget 표시

### 7단계 — 음성과 Gmail
- [ ] 음성 최초 사용 시 브라우저 서비스 외부 처리 안내
- [ ] 메일 데이터 처리 동의
- [ ] 메일 작업에 공개 검색 도구 제공 안 함
- [ ] message_history·DBOS·trace에 메일 원문이 남는 경로 검증, 필요하면 PAYLOAD_ENCRYPTION_KEY로 암호화. 안 되면 기능 플래그로 끔
- [ ] 재연결·권한 철회 처리

### 8단계 — 평가와 관찰
- [ ] TELEMETRY_ENABLED(명시적 설정 없으면 전송 안 함), LOGFIRE_TOKEN 없을 때 OTLP 우회 전송 확인
- [ ] eval 사례를 BUILD_SPEC 16절 30개로 맞춤(고정 날짜·fixture·기억 상태), 안전 지표 분리 보고
- [ ] 실행 기록(날짜·모델·프롬프트 해시·코드 버전·데이터셋 버전)

### 9단계 — 배포와 운영
- [ ] 다크 모드, PWA 아이콘 확인
- [ ] 폴링: 승인 대기 느리게, 보이지 않을 때 줄이기
- [ ] 배포, 마이그레이션·백업·복원·롤백 절차(docs/OPERATIONS.md), 7일 실사용 기록 양식
- [ ] docs/STATE_MACHINES.md(task, operation, approval 전이 표)
- [ ] `GET /usage/daily` 이름 맞추기(지금 `GET /api/usage`)
