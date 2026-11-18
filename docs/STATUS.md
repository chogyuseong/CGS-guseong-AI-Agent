# 진행 상태

BUILD_SPEC 19절 형식. 단계가 끝날 때마다 고친다.

생활 비서 C단계(2026-11-18) — 영화 회차 고르기(compare_showtimes, 붙여 넣은 시간표 + 이동 시간 → 1순위·차선·제외 이유, 예매 링크). 공식 회차 API 없음. 가짜 모델·녹화 응답·실제 브라우저로 확인.

생활 비서 B단계(2026-11-18) — 출발 시각 계산(plan_departure, TMAP 대중교통 + 카카오 좌표, 채팅 결과 카드·지도 링크). 녹화 응답·가짜 모델·실제 브라우저로 확인했고, 2026-11-18 TMAP 키로 실제 1회 조회도 성공했다(경로 없음·오류 응답 모양은 미확인).

생활 비서 A단계(2026-11-18) — (주의: 쿠팡 API 키는 파트너스 최종 승인(링크 매출 15만 원 이상 + 채널 심사) 뒤에만 발급, docs/TOOLS.md) 쿠팡 목표가 감시(감시 전용 `coupang_price`, "샀어요 · 감시 끄기")를 더했다. 쿠팡 상품 조건 비교를 채팅에 연결했다. 모델·공급자 대역으로 가격·옵션과 오류를 검사하고 브라우저의 가격·출처 링크 표시를 확인했다. 파트너스 키 설정과 실제 계정의 검색 권한 확인이 남았다. 목표가 감시는 후속 단계다.

현재 단계(2026-11-18): BUILD_SPEC 기준 9단계 보강 중 코드·문서 부분 완료(GET /usage/daily, STATE_MACHINES·OPERATIONS 문서,
로컬 복원 연습, 다크 모드, PWA 아이콘, 폴링 간격). 9단계는 실제 배포·배포 환경 복원·실기기·7일 운영이 남아 **미완료**다.
8단계도 실제 eval 실행은 사용자가 사례 초안을 확인하고 예상 비용을 승인한 뒤에 한다(아직 실행하지 않음, 측정값 없음).

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
- 5·6단계 보강(2026-11-17): 아래 차이 목록 참고
- 7단계 보강(2026-11-17): 음성 최초 사용 안내, 메일 데이터 처리 동의, 메일 작업의 공개 검색 제외, 복구 payload 암호화
  (PAYLOAD_ENCRYPTION_KEY: DBOS 기록 전체·모델 기록 속 메일, 키 없으면 메일 기능 꺼짐), steps에 메일 내용 없음,
  재연결 필요 상태·Gmail 범위 부족 처리. 마이그레이션 11dcb5772dd1. 이어서 메일을 읽은 문맥의 예약·감시 생성 제외(사용자 결정 a)
- 8단계 보강(2026-11-17): TELEMETRY_ENABLED(명시해야 전송)와 OTLP 환경변수 우회 차단, eval 사례를 BUILD_SPEC 16절 30개로
  다시 씀(고정 시각 2026-11-18 09:00 서울·fixture·기억 상태, 복수 허용 경로·되묻기, 인자·대상·결과 주장·출처 평가),
  안전 경계 5개와 모델 판단 8개 분리 보고, 실행 기록(날짜·모델·프롬프트 해시·코드 버전·데이터셋 버전·표본·실행 횟수).
  `app/clock.py`(eval 시각 고정). 마이그레이션·웹 변경 없음

실제 검증한 항목(2026-11-17, 8단계 보강 끝): API pytest 605개, ruff·pyright 0건. `uv run python -m evals.run_evals`(모델 호출 없음)
출력: 30건 × 1회 하한 약 $0.0394 / 상한 약 $0.0665(감시 4회). 실제 모델 eval·실제 Logfire 전송은 하지 않았다.
보강 전 코드에서 OTEL_EXPORTER_OTLP_* 환경변수만으로(토큰 없이) OTLP 내보내기가 만들어지고 전송을 시도하는 것을 테스트로 재현했고,
보강 뒤 같은 테스트 8가지가 통과한다(tests/test_observability.py).

실제 검증한 항목(2026-11-17, 7단계 보강 끝): API pytest 568개, ruff·pyright 0건, 새 마이그레이션 11dcb5772dd1 업·다운·업과
`alembic check` 차이 없음, 웹 Biome 0건·tsc 통과·Vitest 288개, Playwright 40개(실제 API 서버 + Chrome 360px).
실제 Gmail 계정·실제 모델로는 확인하지 않았다(가짜 HTTP·FunctionModel).
이어서 예약·감시 생성 제외(사용자 결정 a) 뒤: API pytest 575개, ruff·pyright 0건(웹·마이그레이션 변경 없음).

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

다음 시작 지점: eval 사례는 2026-11-18 사용자가 확정했다(초안 표시 제거). 예상 비용을 승인하면
실제 eval 첫 실행. 그 다음 9단계 보강. 관찰을 켜려면 .env에 TELEMETRY_ENABLED=true도 넣어야 한다(토큰만으로는 보내지 않음).
개발 .env에는 사용자가 PAYLOAD_ENCRYPTION_KEY를 넣었다(2026-11-17). 배포 환경변수에도 넣어야 메일 기능이 켜진다.
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
- [x] 제안은 구조화된 배열(suggestions: title, proposed_user_message, reason) — app/agent/replies.py, tests/test_suggestions.py. 누르면 입력창에 채우기만
- [x] 예산 부족 시 paused_budget 표시 — 감시 last_result paused_budget, 목록 API budget_paused, 자동화 화면 안내(tests/test_budget_pause.py)
- 완료 증거: 같은 발생 시각 중복 없음·서버 중단 후 폭주 없음(test_occurrences), 숫자 감시 모델 호출 없음·날짜 고정(test_watch_conditions), EXTERNAL 계획 없음(test_automation_runs의 approvals 거부), 예산 부족 표시(test_budget_pause)
- 남긴 것: 채팅 task 전체의 AgentReply(sources·needs_clarification), 예약 시간대는 서울만 만든다(컬럼은 있음)

### 7단계 — 음성과 Gmail
- [x] 음성 최초 사용 시 브라우저 서비스 외부 처리 안내(확인 후 이 브라우저에 기억) — components/ChatInput.test.tsx
  "음성 최초 사용 안내", e2e/voice.spec.ts
- [x] 메일 데이터 처리 동의(users.mail_consent_at, PUT/DELETE /api/connections/google/mail-consent. 동의 전에는 Gmail을 부르지 않음)
  — tests/test_mail_privacy.py(test_mail_tools_refuse_before_consent, test_consent_api_turns_mail_on_and_off),
  components/GoogleConnection.test.tsx "메일 데이터 처리 동의", e2e/connections.spec.ts
- [x] 메일 작업에 공개 검색 도구 제공 안 함(메일을 읽은 문맥이면 web_search·search_places·get_weather를 모델에게 주지 않음,
  outbound_refusal은 두 번째 방어선) — tests/test_mail_privacy.py(test_public_tools_are_hidden_after_reading_mail,
  test_public_tools_stay_hidden_in_the_same_conversation_only, test_refused_mail_call_does_not_hide_search), test_data_policy
- [x] message_history·DBOS·trace에 메일 원문이 남는 경로 검증과 PAYLOAD_ENCRYPTION_KEY 암호화. 키가 없으면 기능 플래그로 메일 끔
  — tests/test_mail_privacy.py(test_mail_text_is_not_stored_in_plaintext: steps 내용 없음·message_history 암호문·dbos 스키마 전 행
  암호문(풀면 원문 있음)·앱 로그 없음, test_next_task_model_sees_decrypted_mail, test_mail_is_disabled_without_payload_key,
  test_recovery_rereads_mail_instead_of_replaying), tests/test_payload_crypto.py, trace는 tests/test_observability.py
  (test_mail_task_trace_has_no_content). 한계: 모델이 쓴 답변(요약)은 화면에 보인 대화라 평문으로 남는다(BUILD_SPEC 5.2)
- [x] 재연결·권한 철회 처리(invalid_grant → "다시 연결 필요"·토큰 삭제·대기 계획 connection_revoked, Gmail 범위 부족 403 → 메일 권한
  기록에서 뺌, 화면 안내) — tests/test_google_reconnect.py, components/GoogleConnection.test.tsx "다시 연결 필요", e2e/connections.spec.ts
- 완료 증거(BUILD_SPEC 13절 7단계): 음성 자동 전송 없음(ChatInput.test·voice.spec), 메일 본문이 검색어로 유출되지 않음(검색 도구 숨김 +
  test_data_policy), 메일 내 지시로 외부 행동·기억 생성 불가(test_mail_flow: 일정 삭제는 승인 대기·감시 mismatch, remember 거부),
  메일 내 지시로 예약 생성 불가(test_mail_automation: 예약·감시 0건), steps·로그·트레이스에 원문 없음(test_mail_privacy,
  test_observability), 복구 저장소의 민감 payload 정책(test_mail_privacy의 dbos 스키마 검사, test_payload_crypto)
- [x] 메일 내 지시로 예약·감시 생성 불가(2026-11-17 사용자 결정 a): 메일을 읽은 문맥(같은 대화의 이전 기록 포함)이면 create_schedule·
  create_watch를 모델에게 주지 않고(visible_tool), 실행 직전 서버 검사(automation_refusal)가 숨긴 도구 호출을 failed(data_policy)로 끝낸다.
  조회·지우기는 그대로 — tests/test_mail_automation.py(test_mail_instruction_cannot_create_schedule: FunctionModel이 메일을 읽고
  create_schedule·create_watch를 불러도 0건, test_server_refuses_schedule_called_together_with_read_mail,
  test_server_refuses_schedule_when_history_has_mail, test_automation_tools_stay_hidden_in_the_same_conversation_only)
- 사용자 판단 5개는 2026-11-17 사용자 답으로 정함(docs/DECISIONS.md "7단계 보강"의 "사용자 결정" 줄). 남은 제안: 검색 결과 문맥의
  예약 생성(BUILD_SPEC 15절 30번)은 미충족(docs/DECISIONS.md "7단계 보강: 남은 것")

### 8단계 — 평가와 관찰
- [x] TELEMETRY_ENABLED(명시적 설정 없으면 전송 안 함), LOGFIRE_TOKEN 없을 때 OTLP 우회 전송 확인 — tests/test_observability.py
  (보강 전 코드에서 OTEL_EXPORTER_OTLP_* 우회를 재현, configure 동안 숨김으로 막음. 내용 수집 비활성화 테스트도 다시 통과)
- [x] eval 사례를 BUILD_SPEC 16절 30개로 맞춤(고정 날짜·fixture·기억 상태), 안전 지표 분리 보고 — tests/test_evals.py,
  tests/test_eval_rules.py(가짜 모델). 사례는 아직 초안(사용자 확인 전), 실제 모델 측정값 없음
- [x] 실행 기록(날짜·모델·프롬프트 해시·코드 버전·데이터셋 버전, 표본·실행 횟수) — evals/record.py, test_run_record_fields
- [ ] (남음, 사용자 확인 후) 실제 eval 실행과 측정값·가장 취약한 유형·개선 전후 비교(BUILD_SPEC 8단계 완료 증거)

### 9단계 — 배포와 운영
- [x] 다크 모드(시스템 설정을 따름, 테마 선택 없음), 상태 색 토큰, 명도 대비 — lib/theme.test.ts(어두운 토큰이
  prefers-color-scheme, 화면 코드에 고정 색 없음), e2e/dark-mode.spec.ts(360px 어둠·밝음에서 로그인·채팅·단계·외부 콘텐츠·
  초안 카드·승인 카드·음성 안내·대화 목록·작업 기록·자동화·기억·설정·연결·비용의 글자 대비 4.5:1(큰 글자 3:1), 배경색).
  밝은 화면에서 찾은 대비 부족 3곳은 토큰을 바꿔 고침(docs/DECISIONS.md). 스크린샷은 저장소 밖에서 눈으로 확인.
  **실기기 확인은 미완료**(docs/PHONE-CHECK.md 10절 "다크 모드")
- [x] PWA manifest와 아이콘 — lib/pwa.test.ts(192·512·maskable 512·apple-touch 180 파일이 있고 PNG 크기가 같음),
  e2e/dark-mode.spec.ts(theme-color 밝음·어둠 meta, apple-touch-icon·manifest 아이콘 주소가 200 image/png). 홈 화면 추가는 실기기 미확인
- [x] 폴링: 실행 중 1초에서 백오프(2·3·5초), 승인 대기 15초, 끝나면 멈춤, 숨은 탭 멈춤·복귀 즉시 갱신 — lib/polling.ts·
  lib/polling.test.ts, components/PendingTask.test.tsx(승인 대기 3.5초 동안 다시 안 가져옴, focusManager로 숨김·복귀). 간격 표는 docs/DECISIONS.md
- [x] `pnpm lint`가 생성 파일 next-env.d.ts 때문에 실패하던 것 — scripts/biome-check.mjs에서 제외
- [x] 마이그레이션·롤백(downgrade 위험), 백업·복원, DBOS 업그레이드(WORKFLOW_VERSION)와 실행 중 작업, 키 교체, 모델/API 장애
  표시·대응, 7일 실사용 기록 양식 — docs/OPERATIONS.md. 모델 장애 화면 문구는 tests/test_outage.py(가짜 모델 529)
- [x] 테스트 DB 복원 연습 — **로컬 docker postgres에서만**(2026-11-18, docs/OPERATIONS.md 7절: pg_dump → 버리는 DB에 복원 →
  41개 표 행 수·인덱스·제약·시퀀스 같음, alembic current head, alembic check 통과 → 삭제). 배포 환경 복원은 미완료
- [x] docs/STATE_MACHINES.md(task 12, operation 12, approval, outbox, 발생 기록, step 전이 표) — tests/test_state_machines.py
  (상태 목록 = enum, 표의 상태·함수 이름이 코드에 있음). 표를 만들며 찾은 버그(끝난 task의 approved operation이 남아 보관 정리에서
  빠짐)를 고침 — tests/test_workflows.py::test_finished_task_leaves_no_approved_operation.
  task의 waiting_input·reconciling·partially_done과 operation planned는 정의만 있고 쓰지 않음(표에 이유)
- [x] `GET /usage/daily` 이름 맞추기 — `GET /api/usage/daily`, 옛 `/api/usage`는 없앰(404). tests/test_usage_api.py,
  화면 lib/usage.ts·UsageReport.test.tsx·e2e 가짜 경로, schema.d.ts 재생성
- [ ] (남음) 실제 배포(HTTPS 주소), 배포 환경에서 로그인·승인·생성·재시작 복구 확인, Railway 백업 기능 확인과 배포 환경 복원 연습
- [ ] (남음) 실기기 키보드·푸시·음성 확인, 7일 실사용 기록(양식만 있음)
