# 구조 (4단계 기준)

마이에이전트는 화면(Next.js)과 에이전트 서버(FastAPI)를 나눈 모노레포다.
1단계까지: 로그인, 모든 테이블, 채팅 메시지 → 에이전트 실행(메모·할 일 도구) → 작업 기록 조회.
2단계: EXTERNAL 도구의 승인(이중 방어), 감시 에이전트, 승인·거절 API(화면의 승인 카드는 별도 작업).
3단계(API): 실행을 DBOS 워크플로로 옮겨 API는 바로 응답(202)하고 서버가 죽었다 살아나도 이어서 실행, 웹 푸시 구독·발송.

## 구성

```text
폰/PC 브라우저
   │  http://localhost:3000 (폰은 http://PC의LAN주소:3000)
   ▼
apps/web  Next.js 16 ── rewrites: /api/* ──▶ apps/api  FastAPI (127.0.0.1:8000)
                                                │  DBOS 3.5 워크플로 ─ Pydantic AI 2.68 Agent ──▶ Anthropic API (Haiku 5.5 / Sonnet 5.5)
                                                │  SQLAlchemy 2.1 비동기 + psycopg 3      └─ pywebpush ──▶ 브라우저 푸시 서비스(FCM, Apple 등)
                                                ▼
                                       PostgreSQL 16 (docker, 127.0.0.1:5432), DB myagent
                                       - public 스키마: 앱 테이블 15개 (Alembic)
                                       - dbos 스키마: DBOS 시스템 테이블(워크플로 상태, 단계 결과). DBOS가 launch 때 만든다
```

- 브라우저는 3000번 하나만 본다. `/api/*`는 Next.js가 FastAPI로 넘기므로 같은 출처(same origin)라 CORS가 필요 없다.
- FastAPI는 127.0.0.1에만 열려 있다. 폰은 Next.js를 통해서만 API에 닿는다.

## apps/api

| 파일 | 역할 |
| --- | --- |
| `app/main.py` | `create_app(settings)`: 앱, 미들웨어, 라우터, 에이전트, 전송 제한기, 워크플로 시작기, 푸시 전송기, 외부 API HTTP 클라이언트(`app.state.http_client`) 조립. lifespan에서 DBOS 시작·복구·종료, HTTP 클라이언트 닫기. uvicorn은 `--factory`로 부른다 |
| `app/push.py` | 웹 푸시: `PushNotifier.notify(user_id, kind, url)`, kind별 고정 문구, 404·410 구독 삭제, pywebpush 전송기 |
| `app/config.py` | `.env`를 읽는 Settings(pydantic-settings), `MODEL_PRICES`(공식 가격) |
| `app/db.py` | 비동기 엔진, 요청마다 세션 하나를 주는 `get_session`/`DbSession` |
| `app/security.py` | bcrypt 비밀번호 해시, 서명된 세션 쿠키, Origin 검사 미들웨어, `CurrentUser`, OAuth 토큰 암호화(`token_cipher`: Fernet, `TOKEN_ENCRYPTION_KEY`) |
| `app/google_auth.py` | Google OAuth(4단계): 동의 화면 주소(state·PKCE), 토큰 교환·갱신·철회(httpx로 REST 직접), 토큰 암호화 저장, 도구용 `access_token(deps)`(만료 1분 전·401이면 갱신, invalid_grant면 연결 삭제) |
| `app/rate_limit.py` | 메시지 전송 제한(사용자당 분당 20회, 메모리 슬라이딩 윈도) |
| `app/users.py`, `app/cli.py` | 사용자 조회·생성, `python -m app.cli create-user`, 시험 푸시 `python -m app.cli send-test-push` |
| `app/event_loop.py` | Windows에서 SelectorEventLoop를 쓰는 이유와 CLI용 `run_async` |
| `app/models/` | 15개 테이블 |
| `app/schemas/` | 요청·응답 Pydantic 모델(웹이 OpenAPI로 타입 생성) |
| `app/routers/` | `/api` 아래: health, auth, conversations, tasks, approvals, push, connections(4단계) |
| `app/agent/` | 에이전트(아래 절) |
| `app/tools/` | 도구. `notes.py`(메모, 할 일), 4단계: `http.py`(외부 API 공통 호출 규칙), `weather.py`, `places.py`, `websearch.py`, `gcal.py`. 엔드포인트·한도 요약은 `docs/TOOLS.md` |
| `alembic/` | 마이그레이션. env.py는 동기 엔진(같은 psycopg 3 드라이버) |
| `tests/` | pytest. DB·DBOS 픽스처는 `tests/conftest.py` 한 곳, 가짜 모델 대본은 `tests/model_scripts.py`(DBOS에 등록된 가짜 모델 하나), 테스트 전용 EXTERNAL 도구는 `tests/fake_tools.py`, 가짜 푸시 전송기는 `tests/fake_push.py`, 가짜 외부 HTTP는 `tests/fake_http.py`, 외부 API 응답은 `tests/fixtures/`(실제 녹화본과 문서 형식으로 만든 것의 구분은 그 폴더의 README.md) |

### API (3단계)

| 메서드 | 경로 | 응답 |
| --- | --- | --- |
| GET | `/api/health` | 200 `{"status": "ok"}` |
| POST | `/api/auth/login` | 200 `{"id", "email"}` + 세션 쿠키 / 401 |
| POST | `/api/auth/logout` | 204 |
| GET | `/api/auth/me` | 200 `{"id", "email"}` / 401 |
| GET | `/api/conversations` | 내 대화 목록(최근 활동순) |
| POST | `/api/conversations` | 201 대화 생성. 본문 `{"title"?}`, 비우면 "새 대화" |
| GET | `/api/conversations/{id}/messages` | 내 대화의 메시지(오래된 순). 남의 대화·없는 대화는 404 |
| POST | `/api/conversations/{id}/messages` | 202 `{"task_id"}`. 본문 `{"content"}`(1~4,000자). **3단계: run_task 워크플로를 시작만 하고 바로 응답**(그 시점 task는 보통 queued). 분당 20회 초과 429 |
| GET | `/api/tasks` | 최근 작업 목록(`?limit=1~100`, 기본 30) |
| GET | `/api/tasks/{id}` | 상태, steps, final_answer, error, approvals(그 작업의 모든 승인, step 순서). 화면은 이것을 1초마다 폴링한다 |
| POST | `/api/approvals/{id}/approve` | 200 `{approval, changed, task_status}`. 본문 `{"confirm_mismatch"?: bool}`(생략 가능). **3단계: 마지막 결정이면 resume_task 워크플로를 시작만 하고 바로 응답**(task_status는 그 시점 상태, 보통 waiting_approval·running). 이미 결정됨 → 200 `changed=false`. 404 `not_found` / 409 `mismatch_confirmation_required`·`task_not_waiting` / 410 `approval_expired` |
| POST | `/api/approvals/{id}/reject` | 승인과 같은 응답. 오류는 404 `not_found` / 409 `task_not_waiting` / 410 `approval_expired`. 모델은 '사용자가 거절함'을 받는다 |
| GET | `/api/push/public-key` | 200 `{"enabled": bool, "public_key": str \| null}`. enabled는 VAPID 세 값(공개 키, 개인 키, VAPID_SUBJECT)이 모두 있을 때만 true |
| POST | `/api/push/subscribe` | 204. 본문은 브라우저 `PushSubscription.toJSON()` 그대로 `{"endpoint", "keys": {"p256dh", "auth"}, "expirationTime"?}`. endpoint는 푸시 서비스의 https 주소만(아니면 422). 같은 endpoint면 키·사용자를 갱신 |
| POST | `/api/push/unsubscribe` | 204. 본문 `{"endpoint"}`. 없거나 남의 것이어도 204 |
| GET | `/api/connections` | 200 `[{"provider": "google", "available", "connected", "calendar", "scopes", "connected_at"}]`(4단계). available은 서버 설정(GOOGLE_* 세 값 + 유효한 TOKEN_ENCRYPTION_KEY)이 갖춰졌을 때만 true. 토큰은 응답에 없다 |
| GET | `/api/connections/google/start` | 302 Google 동의 화면(4단계). 화면은 fetch가 아니라 **링크 이동**으로 연다. state·PKCE를 세션 쿠키에 저장. 설정이 없으면 302 `{APP_ORIGIN}/connections?google=error&reason=not_configured`. 로그인 안 했으면 401 |
| GET | `/api/connections/google/callback` | Google이 브라우저를 보내는 주소(4단계). 302 `{APP_ORIGIN}/connections?google=connected` 또는 `?google=error&reason=<코드>`(not_logged_in, access_denied, google_error, invalid_state, invalid_request, exchange_failed, scope_missing, not_configured) |
| DELETE | `/api/connections/{provider}` | 204(4단계, provider는 `google`만, 그 밖은 404). Google 쪽 권한 철회를 시도하고 실패해도 토큰을 지운다. 연결이 없어도 204 |
| GET | `/api/openapi.json`, `/api/docs` | OpenAPI 문서 |

승인 API 오류의 본문은 `{"detail": {"code": "...", "message": "한국어 문장"}}`이고 OpenAPI에 `ApprovalErrorOut`(`detail: ApprovalErrorDetail{code, message}`) 모델로 드러난다(3단계).
푸시 API는 모두 로그인 필요(401)이고, POST는 Origin 검사를 받는다.
`approvals[]` 한 건: `id, step_id, tool_name, tool_input, status(pending|approved|rejected|expired), summary(preview), expires_at,
decided_at, guard({verdict: match|mismatch|unsure, reason} | null), needs_mismatch_confirmation`.
task 상태 `waiting_approval`, step 상태 `waiting_approval`·`rejected`·`expired`(2단계 추가)가 실제로 쓰인다.

상태를 바꾸는 요청(GET/HEAD/OPTIONS 외)은 `Origin`이 `APP_ORIGIN`과 다르거나 없으면 403이다. 모든 조회에 user_id 조건, 남의 것은 404.

## 에이전트 (app/agent)

### 요청 한 건의 흐름 (3단계)

```text
POST /api/conversations/7/messages {"content": "우유 사기 할 일에 추가해줘"}
  1. messages에 user 메시지, tasks에 1행(origin=chat, status=queued) → 커밋
  2. run_task 워크플로 시작(워크플로 ID "task-42") → 바로 202 {"task_id": 42}
     화면은 GET /api/tasks/42를 1초마다, 끝나면 GET .../messages를 다시 가져온다
  3. 워크플로 run_task (workflows.py → runner.run_task). [단계]는 DBOS가 결과를 기록해 복구 때 다시 실행하지 않는다
     a. [claim_task]   queued → running (SELECT ... FOR UPDATE), tasks.workflow_id="task-42"
     b. [prepare_run]  오늘(Asia/Seoul) 비용 합계 ≥ DAILY_COST_LIMIT_USD 이면 모델을 부르지 않고 budget_exceeded.
                       같은 대화의 이전 task 기록(도구 호출·결과 포함)을 최근 10개·40,000자 안에서 이어 붙임
     c. agent.run(prompt, toolsets=origin별, usage_limits=요청 수·토큰 상한)
          - SelectModel: 이번 task의 도구 호출 ≤4 → Haiku 5.5, >4 → Sonnet 5.5
          - [모델 요청]마다 DBOS 단계(DBOSDurability)
          - 모델이 add_todo 호출 → StepTracking이 steps 1행(running→done) 기록하며 도구 실행 → todos 1행
            (도구 실행은 DBOS 단계가 아니다. 복구 때는 tool_call_id로 steps를 보고 다시 실행하지 않는다)
          - 모델이 최종 답변
     d. [finish_task]  한 트랜잭션에서: task(done, final_answer, message_history, request_count, total_tokens),
                       usage_logs(모델 응답마다 1행), assistant 메시지 1행. task가 이미 running이 아니면 아무것도 안 함
     e. [notify]       승인 필요·실패는 항상, 완료는 20초 넘게 걸렸을 때만 푸시(고정 문구)
```

### 승인이 필요한 요청 (2단계 + 3단계 워크플로)

```text
POST .../messages {"content": "외부 도구로 ○○ 해줘"}   → 바로 202
  run_task 워크플로 → agent.run → 모델이 EXTERNAL 도구 호출
     requires_approval이라 실행하지 않고 run 종료, output = DeferredToolRequests
  [finish_task]
     a. 호출마다 preview 문장(TOOL_PREVIEWS) → 감시 에이전트(요청 문장, 도구 이름, 입력, preview만) → match|mismatch|unsure
     b. 한 트랜잭션: steps(waiting_approval) + approvals(pending, 30분) + guard_checks + usage_logs(agent, guard)
        + task(waiting_approval, 결과 없는 도구 호출까지 기록). assistant 메시지는 남기지 않는다
  [notify] approval_needed 푸시. 워크플로는 여기서 끝난다(승인 대기 중에는 도는 워크플로가 없다)
  화면은 폴링으로 approvals[]의 pending 카드를 그린다

POST /api/approvals/{id}/approve (또는 reject)
  approvals.decide: task 행 → 승인 행 FOR UPDATE, pending일 때만 변경, 만료면 410, mismatch는 confirm_mismatch 필요
  pending이 더 없고 task가 waiting_approval이면 resume_task 워크플로 시작(ID "task-42-resume-1") → 바로 200
  resume_task 워크플로 (runner.resume_task)
     a. [claim_resume]   waiting_approval + pending 없음일 때만 running, workflow_id="task-42-resume-1" (동시에 여러 번 와도 한 번)
     b. [prepare_resume] 하루 비용 검사, 이전 대화 기록 + 이 task 기록,
                         DeferredToolResults(승인=ToolApproved, 거절=ToolDenied('사용자가 거절함')), 앞선 run 사용량
     c. agent.run. 승인된 도구: StepTracking hook → 도구 첫 줄 assert_approved(DB 확인, step running) → 실행 → step done
     d. [finish_task] 최종 답변 저장(task done, assistant 메시지 1개). 또 EXTERNAL을 부르면 다시 waiting_approval
        (다음 재개는 "task-42-resume-2"). 승인됐지만 실행 못 하고 끝나면 그 step은 failed("실행하지 않았습니다")
     e. [notify]
```

### 내구성 실행 (DBOS 3.5.0 + Pydantic AI 2.68 DBOS 통합, 설치된 버전의 소스·docstring으로 확인)

| 개념 | 이 버전의 사용법 | 이 앱에서 |
| --- | --- | --- |
| 시작 | `DBOS(config=DBOSConfig)` 후 `DBOS.launch()`. 끝낼 때 `DBOS.destroy()` | `main.py` lifespan(실행 중인 루프 안). `system_database_url`=앱 DB, `dbos_system_schema="dbos"` |
| 워크플로 | `@DBOS.workflow(name=...)` 붙인 함수. 서버가 죽으면 다음 launch 때 처음부터 다시 실행되고, 끝난 단계는 기록된 결과로 건너뛴다 | `run_task`, `resume_task` (`app/agent/workflows.py`) |
| 단계(step) | `@DBOS.step()` 또는 `await DBOS.run_step_async({"name": ...}, 함수, *인자)`. 결과를 pickle로 저장. 단계 순서가 기록과 다르면 `DBOSUnexpectedStepError` | `durable_step`(`app/agent/durable.py`)로 claim·prepare·finish·notify를 감쌌다 |
| 워크플로 ID | `with SetWorkflowID("id"): await DBOS.start_workflow_async(fn, ...)`. 같은 ID로 다시 시작하면 실행하지 않고 기존 워크플로 핸들을 준다 | `task-{id}`, `task-{id}-resume-{n}` |
| 결과 기다리기 | `handle.get_result()`, `DBOS.retrieve_workflow_async(id)` | 운영 코드는 기다리지 않는다. 테스트의 WaitingLauncher만 기다린다 |
| 예약 실행 | `DBOS.create_schedule(schedule_name=, workflow_fn=, schedule="cron식", cron_timezone=)` 또는 `apply_schedules`. 워크플로는 `(예약 시각, context)`를 받고, ID는 DBOS가 `sched-{이름}-{시각}`으로 정해 같은 시각이 두 번 돌지 않는다 | 6단계(예약 검사 1분마다)에서 쓴다. 3.5에는 옛 `@DBOS.scheduled` 데코레이터가 없다 |
| 복구 | launch 때 같은 executor("local")·같은 application_version의 PENDING 워크플로를 다시 실행. 기본 100번까지 | `application_version="myagent-3"`으로 고정(코드가 바뀌어도 복구). 워크플로 단계 구성을 바꾸면 올린다 |
| Pydantic AI 통합 | `Agent(name=..., capabilities=[DBOSDurability()])`를 launch 전에 만들고, 우리 `@DBOS.workflow` 안에서 `agent.run()`. 모델 요청이 단계가 되고, 함수 도구·hook은 단계가 아니다. 실행 중 모델 객체를 바꾸려면 `models=`에 등록해야 한다(문자열은 등록 불필요). `DBOSAgent` 래퍼는 없어질 예정 | `agent.py`. 도구 중복 실행 방지는 StepTracking의 tool_call_id 검사 |

- Windows: DBOS는 루프 밖에서 launch하면 자기 루프(`asyncio.new_event_loop()` = Proactor)를 만들어 psycopg 비동기가 안 된다.
  그래서 서버는 lifespan(실행 중인 SelectorEventLoop) 안에서 launch해 DBOS가 그 루프를 쓰게 했다. 테스트는 정책을 Selector로 바꾼다.
- DBOS 시스템 테이블은 `myagent` DB의 `dbos` 스키마에 생긴다(Alembic은 public만 보므로 `alembic check`에 영향 없음).
  지우려면 서버를 끄고 `docker exec my-agent-postgres-1 psql -U myagent -d myagent -c "DROP SCHEMA dbos CASCADE"`.
  **앱 테이블을 초기화(DB 재생성 등)할 때는 dbos 스키마도 함께 지운다.** 남겨 두면 새 task의 ID가 옛 워크플로 ID("task-1")와
  겹쳐 DBOS가 이미 끝난 워크플로로 보고 실행하지 않는다.
- 서버 시작 때 `restart_orphaned_tasks`가 "task는 커밋됐는데 워크플로를 시작하기 전에 죽은" 경우를 잇는다
  (queued chat task → run 시작, pending 없는 waiting_approval → 재개 시작. 같은 ID라 중복 실행은 없다).
- 강제 종료 복구를 직접 확인하는 방법: `docs/DURABILITY-CHECK.md`.

### 파일

| 파일 | 역할 |
| --- | --- |
| `workflows.py` | DBOS 워크플로 `run_task`·`resume_task`, 워크플로 ID 규칙, `WorkflowLauncher`(app.state.launcher), 재개 시작(`start_resume_if_waiting`), 시작 때 고아 task 잇기, DBOS 설정·launch·종료 |
| `durable.py` | `durable_step(name, fn, *args)`: 워크플로 안이면 DBOS 단계, 밖이면 그냥 실행 |
| `runner.py` | `run_task`(실행), `resume_task`(승인 뒤 재개)를 단계(claim → prepare → agent.run → finish → notify)로 구성. 승인 대기면 preview·감시·승인 요청 저장. 어떤 예외도 잡아 failed/budget_exceeded로 끝낸다(HTTP를 모름) |
| `agent.py` | Agent 정의: output_type=[str, DeferredToolRequests], instructions(시스템 프롬프트 + 오늘 날짜), capabilities(ResolveModelId, SelectModel, StepTracking, DBOSDurability) |
| `approvals.py` | 승인 요청 저장(`save_requests`), 결정(`decide`, FOR UPDATE), 재개 결과(`build_results`), 두 번째 방어선(`assert_approved`) |
| `guard.py` | 감시 에이전트(도구 없음, AGENT_MODEL_GUARD). 입력 4가지만, 실패는 unsure |
| `tracking.py` | 도구 실행 hook(wrap_tool_execute)으로 steps 기록, 예상 못 한 예외를 ok=false로 바꿈. 복구 때 끝난 호출은 기록된 결과를 재생, 도중에 끊긴 INTERNAL·EXTERNAL 호출은 다시 실행하지 않고 failed("실행 여부를 확인할 수 없음") |
| `toolsets.py` | read_tools / internal_tools / external_tools(일정 쓰기 셋, requires_approval, `build_external_toolset`) 와 origin별 조합, step 문구(TOOL_LABELS), 승인 카드 문장(TOOL_PREVIEWS), 외부 콘텐츠 도구 목록(EXTERNAL_CONTENT_TOOLS) |
| `model_choice.py` | FAST/SMART 선택 규칙, 설정의 API 키로 AnthropicModel 만들기 |
| `budget.py` | UsageLimits, 비용 계산(MODEL_PRICES), 하루 비용 검사 |
| `history.py` | 이전 기록 저장 형식(JSON), 자르기, 실패한 run 기록 정리, 승인 대기 기록 저장·결과 없는 호출 찾기 |
| `prompts.py` | 시스템 프롬프트(명세 문장 + 승인 카드 재확인 금지), 동적 지시문(오늘 날짜·요일, 이번 주 날짜표, 다음 주 월요일) |
| `deps.py`, `results.py` | AgentDeps(user_id, task_id, origin, db 세션 공장, settings, http), ToolResult(`external()`: 지시문 무시 안내로 감싸고 6,000자로 자름) |

### 도구 (1단계 + 4단계)

| 도구 | 등급 | 하는 일 |
| --- | --- | --- |
| `list_todos(include_done=false)` | READ | 할 일 목록(최대 50) |
| `list_notes(limit=10)` | READ | 최근 메모 |
| `add_todo(title)` | INTERNAL | 할 일 추가 |
| `complete_todo(todo_id)` | INTERNAL | 할 일 완료 표시(이미 완료면 그대로 성공) |
| `save_note(content)` | INTERNAL | 메모 저장 |
| `get_weather(place, date)` | READ, 외부 콘텐츠 | 카카오로 좌표 변환 → Open-Meteo 예보(최저·최고, 강수 확률, 3시간 간격). 오늘~15일 뒤 |
| `search_places(query, near?, radius_m=2000, size=5)` | READ, 외부 콘텐츠 | 카카오 로컬 키워드 검색. 이름·주소·카테고리·전화·링크·좌표·거리 |
| `web_search(query, size=5)` | READ, 외부 콘텐츠 | NAVER API HUB 웹문서 검색. 제목·요약·링크(태그 제거) |
| `list_events(date_from, date_to)` | READ, 외부 콘텐츠 | Google 기본 캘린더 일정(최대 31일, 50개). id·제목·시작·끝·종일·장소 |
| `create_event(title, start, end, location?, description?)` | EXTERNAL | 일정 만들기. 시각은 서울 `YYYY-MM-DDTHH:MM` |
| `update_event(event_id, title?, start?, end?, location?, description?)` | EXTERNAL | 바꿀 칸만 PATCH |
| `delete_event(event_id)` | EXTERNAL | 일정 삭제 |

origin이 chat이면 3개 toolset 전부, schedule·watch면 read_tools만 넘긴다(일정 쓰기 도구는 자동 작업에 없다).
외부 API 호출 규칙(타임아웃 10초, GET만 1회 재시도, 쓰기는 재시도 없음, 오류 문장에 키·본문 없음)은 `app/tools/http.py` 한 곳에 있다.

## 테이블 (15개)

| 묶음 | 테이블 |
| --- | --- |
| 계정 | users |
| 대화 | conversations, messages |
| 실행 기록 | tasks, steps, approvals, guard_checks, usage_logs |
| 개인 데이터 | memories, notes, todos |
| 자동화 | scheduled_jobs, watches |
| 외부 연결 | connections, push_subscriptions |

- 모든 테이블에 `id`, `created_at`, `updated_at`이 있다(`TimestampedTable`).
- 상태 값은 Python `StrEnum` + DB의 VARCHAR와 CHECK 제약이다(`app/models/enums.py`).
- `messages.task_id`와 `tasks.trigger_message_id`는 서로를 가리킨다. 마이그레이션에서 tasks를 만든 뒤 FK를 따로 추가한다.
- `usage_logs.user_id`(1단계 추가): 하루 비용 상한을 사용자별로 합산한다.
- `steps.status`에 `expired`(2단계 추가): 승인을 30분 안에 결정하지 않아 실행하지 않은 호출.
- `tasks.workflow_id`(3단계부터 채움): 그 task를 지금(마지막으로) 실행한 DBOS 워크플로 ID. 다음 재개 번호를 여기서 계산한다.
- `push_subscriptions`(3단계부터 사용): endpoint UNIQUE. 발송 성공 때 `last_used_at`, 404·410이면 행 삭제.
- EXTERNAL 호출 한 번 = steps 1행 + approvals 1행(tool_call_id UNIQUE) + guard_checks 1행.
- `connections`(4단계부터 사용): `(user_id, provider)` UNIQUE. `encrypted_token`은 `{access_token, refresh_token, expires_at}` JSON을
  Fernet으로 암호화한 문자열, `scopes`·`expires_at`(access token 만료)은 평문. 갱신이 invalid_grant로 실패하면 행을 지운다. 마이그레이션 변경 없음.
- 사용자 메시지와 assistant 답변 모두 `messages.task_id`로 task에 연결된다.
