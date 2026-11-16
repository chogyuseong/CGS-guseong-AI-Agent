# 구조 (1단계 기준)

마이에이전트는 화면(Next.js)과 에이전트 서버(FastAPI)를 나눈 모노레포다.
1단계까지: 로그인, 모든 테이블, 채팅 메시지 → 에이전트 실행(메모·할 일 도구) → 작업 기록 조회.
승인과 감시 에이전트는 2단계, 내구성 실행(DBOS)과 푸시는 3단계에서 만든다.

## 구성

```text
폰/PC 브라우저
   │  http://localhost:3000 (폰은 http://PC의LAN주소:3000)
   ▼
apps/web  Next.js 16 ── rewrites: /api/* ──▶ apps/api  FastAPI (127.0.0.1:8000)
                                                │  Pydantic AI 2.68 Agent ──▶ Anthropic API (Haiku 5.5 / Sonnet 5.5)
                                                │  SQLAlchemy 2.1 비동기 + psycopg 3
                                                ▼
                                       PostgreSQL 16 (docker, 127.0.0.1:5432)
                                       - 앱 테이블 15개 (Alembic)
                                       - (3단계) DBOS 시스템 테이블
```

- 브라우저는 3000번 하나만 본다. `/api/*`는 Next.js가 FastAPI로 넘기므로 같은 출처(same origin)라 CORS가 필요 없다.
- FastAPI는 127.0.0.1에만 열려 있다. 폰은 Next.js를 통해서만 API에 닿는다.

## apps/api

| 파일 | 역할 |
| --- | --- |
| `app/main.py` | `create_app(settings)`: 앱, 미들웨어, 라우터, 에이전트, 전송 제한기 조립. uvicorn은 `--factory`로 부른다 |
| `app/config.py` | `.env`를 읽는 Settings(pydantic-settings), `MODEL_PRICES`(공식 가격) |
| `app/db.py` | 비동기 엔진, 요청마다 세션 하나를 주는 `get_session`/`DbSession` |
| `app/security.py` | bcrypt 비밀번호 해시, 서명된 세션 쿠키, Origin 검사 미들웨어, `CurrentUser` |
| `app/rate_limit.py` | 메시지 전송 제한(사용자당 분당 20회, 메모리 슬라이딩 윈도) |
| `app/users.py`, `app/cli.py` | 사용자 조회·생성, `python -m app.cli create-user` |
| `app/event_loop.py` | Windows에서 SelectorEventLoop를 쓰는 이유와 CLI용 `run_async` |
| `app/models/` | 15개 테이블 |
| `app/schemas/` | 요청·응답 Pydantic 모델(웹이 OpenAPI로 타입 생성) |
| `app/routers/` | `/api` 아래: health, auth, conversations, tasks |
| `app/agent/` | 에이전트(아래 절) |
| `app/tools/` | 도구. 1단계는 `notes.py`(메모, 할 일) |
| `alembic/` | 마이그레이션. env.py는 동기 엔진(같은 psycopg 3 드라이버) |
| `tests/` | pytest. DB 픽스처는 `tests/conftest.py` 한 곳, 가짜 모델 대본은 `tests/model_scripts.py` |

### API (1단계)

| 메서드 | 경로 | 응답 |
| --- | --- | --- |
| GET | `/api/health` | 200 `{"status": "ok"}` |
| POST | `/api/auth/login` | 200 `{"id", "email"}` + 세션 쿠키 / 401 |
| POST | `/api/auth/logout` | 204 |
| GET | `/api/auth/me` | 200 `{"id", "email"}` / 401 |
| GET | `/api/conversations` | 내 대화 목록(최근 활동순) |
| POST | `/api/conversations` | 201 대화 생성. 본문 `{"title"?}`, 비우면 "새 대화" |
| GET | `/api/conversations/{id}/messages` | 내 대화의 메시지(오래된 순). 남의 대화·없는 대화는 404 |
| POST | `/api/conversations/{id}/messages` | 202 `{"task_id"}`. 본문 `{"content"}`(1~4,000자). 1단계는 실행이 끝난 뒤 응답. 분당 20회 초과 429 |
| GET | `/api/tasks` | 최근 작업 목록(`?limit=1~100`, 기본 30) |
| GET | `/api/tasks/{id}` | 상태, steps, final_answer, error, approvals(1단계는 빈 배열) |
| GET | `/api/openapi.json`, `/api/docs` | OpenAPI 문서 |

상태를 바꾸는 요청(GET/HEAD/OPTIONS 외)은 `Origin`이 `APP_ORIGIN`과 다르거나 없으면 403이다. 모든 조회에 user_id 조건, 남의 것은 404.

## 에이전트 (app/agent)

### 요청 한 건의 흐름 (1단계)

```text
POST /api/conversations/7/messages {"content": "우유 사기 할 일에 추가해줘"}
  1. messages에 user 메시지, tasks에 1행(origin=chat, status=queued) → 커밋
  2. runner.run_task
     a. queued → running (SELECT ... FOR UPDATE)
     b. 오늘(Asia/Seoul) 비용 합계 ≥ DAILY_COST_LIMIT_USD 이면 모델을 부르지 않고 budget_exceeded
     c. 같은 대화의 이전 task 기록(도구 호출·결과 포함)을 최근 10개·40,000자 안에서 이어 붙임
     d. agent.run(prompt, toolsets=origin별, usage_limits=요청 수·토큰 상한)
          - SelectModel: 이번 task의 도구 호출 ≤4 → Haiku 5.5, >4 → Sonnet 5.5
          - 모델이 add_todo 호출 → StepTracking이 steps 1행(running→done) 기록하며 도구 실행 → todos 1행
          - 모델이 최종 답변
     e. 한 트랜잭션에서: task(done, final_answer, message_history, request_count, total_tokens),
        usage_logs(모델 응답마다 1행), assistant 메시지 1행
  3. 202 {"task_id"} → 화면은 GET /api/tasks/{id}와 GET .../messages로 결과를 그린다
```

### 파일

| 파일 | 역할 |
| --- | --- |
| `runner.py` | task 하나 실행. 어떤 예외도 잡아 failed/budget_exceeded로 끝낸다(HTTP를 모름, 3단계 워크플로가 그대로 부름) |
| `agent.py` | Agent 정의: instructions(시스템 프롬프트 + 오늘 날짜), capabilities(ResolveModelId, SelectModel, StepTracking) |
| `tracking.py` | 도구 실행 hook(wrap_tool_execute)으로 steps 기록, 예상 못 한 예외를 ok=false로 바꿈 |
| `toolsets.py` | read_tools / internal_tools / external_tools(비어 있음, requires_approval) 와 origin별 조합, step 문구 |
| `model_choice.py` | FAST/SMART 선택 규칙, 설정의 API 키로 AnthropicModel 만들기 |
| `budget.py` | UsageLimits, 비용 계산(MODEL_PRICES), 하루 비용 검사 |
| `history.py` | 이전 기록 저장 형식(JSON), 자르기, 실패한 run 기록 정리 |
| `prompts.py` | 시스템 프롬프트(명세 문장 + 승인 카드 재확인 금지), 오늘 날짜 |
| `deps.py`, `results.py` | AgentDeps(user_id, task_id, origin, db 세션 공장), ToolResult |

### 도구 (1단계)

| 도구 | 등급 | 하는 일 |
| --- | --- | --- |
| `list_todos(include_done=false)` | READ | 할 일 목록(최대 50) |
| `list_notes(limit=10)` | READ | 최근 메모 |
| `add_todo(title)` | INTERNAL | 할 일 추가 |
| `complete_todo(todo_id)` | INTERNAL | 할 일 완료 표시(이미 완료면 그대로 성공) |
| `save_note(content)` | INTERNAL | 메모 저장 |

origin이 chat이면 3개 toolset 전부, schedule·watch면 read_tools만 넘긴다.

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
- 사용자 메시지와 assistant 답변 모두 `messages.task_id`로 task에 연결된다.
