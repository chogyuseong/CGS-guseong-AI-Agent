# 진행 상태

BUILD_SPEC 19절 형식. 단계가 끝날 때마다 고친다.

현재 단계: BUILD_SPEC 기준 1단계 보강 완료(2026-11-17). 다음은 2단계 보강(승인 계획·영수증).

완료한 항목:
- 예전 명세(SPEC.md) 기준 0~9단계 구현(배포 제외). 기능 목록은 README.md
- 0단계 보강(2026-11-17): 서버 저장형 세션, 로그인 실패 속도 제한, APP_ENV, 테스트 DB 보호 확대, .gitattributes,
  docs/BUILD_SPEC.md·STATUS.md·VERSIONS.md
- 1단계 보강(2026-11-17): client_request_id, SMART 전환 제거·호출 제한 시간, 호출별 예산 예약·정산,
  task 상태 12개·취소 API·agent_runs, ToolResult 구조(succeeded/failed/unknown)

실제 검증한 항목(2026-11-17, 두 번째 PC, 1단계 보강 끝):
- API pytest 400개, ruff·pyright 0건, alembic check 차이 없음, 새 마이그레이션 업·다운·업 확인
- 웹 Biome 0건, tsc, Vitest 263개, Playwright 37개(실제 API 서버 + Chrome)
- 0단계 완료 증거와 해당 테스트:
  - health: tests/test_health.py
  - 인증·세션: tests/test_auth.py(로그인, 로그아웃 후 복사한 쿠키 거부, 세션 교체, 해시만 저장, 만료, 실패 제한)
  - Origin 검사: tests/test_auth.py::test_post_with_other_origin_returns_403(다른 출처, Origin 없음)
  - 사용자 간 접근 차단: test_conversations, test_chat_api, test_approvals, test_automation_api, test_memories의 other_users 테스트
  - 360px 로그인 후 탭: e2e/login.spec.ts, e2e/widths.spec.ts
  - 생성된 타입으로 auth/me: apps/web/lib/auth.ts(openapi-fetch) + e2e 로그인
  - DBOS 복구: tests/test_workflows.py(가짜 모델). 실제 프로세스 강제 종료는 docs/DURABILITY-CHECK.md 절차(이번엔 다시 하지 않음)
  - 버전: docs/VERSIONS.md / 비밀 파일: `.env`는 .gitignore 대상(git check-ignore 확인)

미실행 항목과 이유:
- 실제 프로세스 강제 종료 복구: 0단계 보강에서 워크플로 코드를 바꾸지 않아 다시 하지 않음
- HTTPS 개발 접속(폰): 터널 방법만 문서(docs/PUSH-CHECK.md). 실기기 확인은 사용자

사용자 확인이 필요한 항목:
- 이 PC의 .env는 Django판 형식이었다. DATABASE_URL(로컬 docker), SESSION_SECRET(새로 만듦), APP_ORIGIN을 넣고
  모델 이름에 `anthropic:`을 붙이고 TASK_MAX_STEPS→TASK_MAX_REQUESTS, GOOGLE_REDIRECT_URI를 새 주소로 바꿨다.
  VAPID_SUBJECT는 여전히 비어 있다
- 배포-이어하기.txt 2번의 남은 일(Google 콘솔, 푸시, eval, 배포처)

다음 시작 지점: 2단계 보강(아래 차이 목록의 2단계). 1단계에서 남긴 것: AgentReply(6단계로)
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
- [ ] action_plans(operation_id, canonical_args, args_hash, tool_version, connection_id, target_version, 만료 2종, policy_version)
- [ ] approvals를 계획·승인 회차별로. 승인 후 실행 유효 시간(기본 5분)
- [ ] operations, execution_receipts. 완료 표시는 영수증 기준
- [ ] 실행기의 DB 검사 목록(3.3) 전부
- [ ] 재시작과 무관하게 기록이 남는 가짜 외부 서비스(멱등성 키·결과 조회)
- [ ] 승인 카드: 계정·캘린더, 변경 전후, 유효 시간, 감시 배지 문구(일치로 추정/요청과 다를 수 있음/확인 필요)

### 3단계 — 장애 주입·복구·푸시
- [ ] 장애 지점 5곳 주입 테스트와 결과 표
- [ ] unknown/reconciling, 결과 재조회, 성공 operation 영수증 재사용
- [ ] outbox(task 저장↔워크플로 등록, 승인↔재개, 예약 발생↔task) 또는 DBOS 트랜잭션 연계 확인
- [ ] 취소와 실행 경합
- [ ] 푸시: notify(user_id, kind, internal_url, notification_key), notifications 테이블, 404·410 구독 정리, 짧은 채팅 완료엔 푸시 안 함

### 4단계 — 실제 조회와 캘린더
- [ ] create_event: 안정적인 event ID, 중복 응답·타임아웃 구분
- [ ] update/delete_event: ETag 조건부 요청, 대상 변경 시 stale → 재조회 → 재승인
- [ ] 참석자 있는 일정 수정·삭제 거부, 반복 일정 시리즈 변경 제외
- [ ] draft_message 도구(INTERNAL, 초안만, 복사 버튼)
- [ ] OAuth: 시작을 POST로, state를 DB에 세션·만료·단일 사용으로, 연결 해제 시 대기 중 계획 무효화
- [ ] 도구 정의에 데이터 전송 등급(public/user_private/mail_private/secret)과 정책
- [ ] TOOL 규약을 BUILD_SPEC 17절 체크리스트로

### 5단계 — 기억과 데이터 삭제
- [ ] memories에 source_message_id, source_quote. 원문에 실제로 있는지 서버 검사
- [ ] "외부 도구를 썼으면 저장 금지"(MEMORY_BLOCKING_TOOLS) 제거(BUILD_SPEC 5.4가 금지)
- [ ] 활성 기억 최대 200개, 한 줄 300자
- [ ] 대화 삭제(DELETE /conversations/{id})와 관련 기록 정리
- [ ] 실행 기록 보관 기간(EXECUTION_RETENTION_DAYS 7, RECEIPT_RETENTION_DAYS 90)과 정리
- [ ] docs/DATA_POLICY.md(저장소별 보관·삭제 범위, 백업 지연)

### 6단계 — 예약과 감시
- [ ] 계정 생성만으로 브리핑 예약을 만들지 않음. 사용자가 켠 뒤 실행(지금은 07:30 자동 생성)
- [ ] automation_occurrences로 (job/watch, scheduled_for) 중복 방지, 누락은 유예 시간 안 1건만
- [ ] 예약 하루 1회 이하, weekdays·time_of_day·timezone
- [ ] 감시: 숫자 조건 서버 비교(LLM 없음), 판정 오류는 unknown, 재알림 최소 12시간, 단발성 날짜 고정
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
