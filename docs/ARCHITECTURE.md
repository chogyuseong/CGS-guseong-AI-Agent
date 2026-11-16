# 구조 (초안, 0단계 기준)

마이에이전트는 화면(Next.js)과 에이전트 서버(FastAPI)를 나눈 모노레포다. 0단계에는 로그인과 빈 화면, 모든 테이블만 있다.
에이전트, 도구, 승인은 1단계부터 만든다.

## 구성

```text
폰/PC 브라우저
   │  http://localhost:3000 (폰은 http://PC의LAN주소:3000)
   ▼
apps/web  Next.js 16 ── rewrites: /api/* ──▶ apps/api  FastAPI (127.0.0.1:8000)
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
| `app/main.py` | `create_app(settings)`: 앱, 미들웨어, 라우터 조립. uvicorn은 `--factory`로 부른다 |
| `app/config.py` | `.env`를 읽는 Settings(pydantic-settings), `MODEL_PRICES` |
| `app/db.py` | 비동기 엔진, 요청마다 세션 하나를 주는 `get_session`/`DbSession` |
| `app/security.py` | bcrypt 비밀번호 해시, 서명된 세션 쿠키, Origin 검사 미들웨어, `CurrentUser` |
| `app/users.py` | 이메일 정규화, 사용자 조회·생성 (로그인과 create-user가 같이 씀) |
| `app/cli.py` | `python -m app.cli create-user` |
| `app/event_loop.py` | Windows에서 SelectorEventLoop를 쓰는 이유와 CLI용 `run_async` |
| `app/models/` | 15개 테이블 (아래) |
| `app/schemas/` | 요청·응답 Pydantic 모델 |
| `app/routers/` | `/api` 아래 라우터: health, auth, conversations(읽기 전용) |
| `alembic/` | 마이그레이션. env.py는 동기 엔진(같은 psycopg 3 드라이버) |
| `tests/` | pytest. DB 픽스처는 `tests/conftest.py` 한 곳에 모음 |

### 0단계 API

| 메서드 | 경로 | 응답 |
| --- | --- | --- |
| GET | `/api/health` | 200 `{"status": "ok"}` |
| POST | `/api/auth/login` | 200 `{"id", "email"}` + 세션 쿠키 / 401 |
| POST | `/api/auth/logout` | 204 |
| GET | `/api/auth/me` | 200 `{"id", "email"}` / 401 |
| GET | `/api/conversations` | 내 대화 목록 (읽기 전용) |
| GET | `/api/conversations/{id}/messages` | 내 대화의 메시지. 남의 대화·없는 대화는 404 |
| GET | `/api/openapi.json`, `/api/docs` | OpenAPI 문서 |

상태를 바꾸는 요청(GET/HEAD/OPTIONS 외)은 `Origin`이 `APP_ORIGIN`과 다르거나 없으면 403이다.

### 테이블 (15개)

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

## 요청 한 건의 흐름 (1~3단계에서 만들 예정)

SPEC.md의 "요청 한 건 따라가기"를 따른다. 메시지 전송 → Task(queued) → DBOS 워크플로 → 모델·도구 호출마다 Step 기록 →
EXTERNAL 도구는 승인 대기(Approval) → 승인 후 재개 → 답변과 사용량 기록.
