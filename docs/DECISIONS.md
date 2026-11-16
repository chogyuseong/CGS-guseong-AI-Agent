# 설계 결정 기록

한 줄에 하나씩, 날짜와 이유를 함께 쌓는다. 바뀐 결정은 지우지 않고 새 줄로 덧붙인다.

## 0단계 (2026-11-16)

### 사용자가 정한 것
- 2026-11-16 세션은 서명된 쿠키(Starlette SessionMiddleware)로 한다. 서버 저장형 세션 테이블을 만들면 명세의 15개 테이블을 넘기 때문이다.
- 2026-11-16 세션 쿠키는 HttpOnly, SameSite=Lax이고 Secure는 `APP_ORIGIN`이 https일 때만 켠다. 명세는 Secure를 요구하지만 0단계 완료 기준인 "같은 와이파이의 폰에서 HTTP로 로그인"과 부딪혀서다(Secure 쿠키는 HTTP에서 저장되지 않는다).
- 2026-11-16 상태를 바꾸는 요청(GET/HEAD/OPTIONS 외)은 Origin 헤더가 `APP_ORIGIN`과 같아야 하고 다르면 403이다(CSRF 방어). 폰에서 확인할 때는 `.env`의 `APP_ORIGIN`을 PC의 LAN 주소(`http://192.168.x.x:3000`)로 바꾸고 API 서버를 다시 켜야 한다.
- 2026-11-16 "다른 사용자의 대화는 404" 테스트를 위해 `GET /api/conversations`와 `GET /api/conversations/{id}/messages`만 읽기 전용으로 만든다. 메시지 전송과 에이전트·도구·승인 코드는 1단계 이후에 만든다.
- 2026-11-16 이 PC에는 make가 없어 실행 스크립트는 루트 `package.json`에 둔다(`pnpm dev` 등).
- 2026-11-16 기본 셸이 Windows PowerShell 5.1이라 문서의 명령은 `&&` 없이 한 줄씩 쓴다. package.json 스크립트 안의 `&&`는 cmd가 실행하므로 쓴다.

### 구현하며 정한 것
- 2026-11-16 DB 드라이버는 psycopg 3 하나로 한다. SQLAlchemy 2.1은 같은 URL(`postgresql+psycopg://`)로 create_async_engine이면 비동기, create_engine이면 동기 드라이버를 고르고, DBOS(Python)는 시스템 DB에 항상 psycopg 3을 쓰기 때문에 앱·Alembic·DBOS가 한 드라이버를 공유한다. asyncpg는 DBOS가 쓰지 않아 드라이버가 둘이 된다.
- 2026-11-16 psycopg 비동기는 Windows 기본 ProactorEventLoop에서 동작하지 않아 서버(`--loop asyncio:SelectorEventLoop`), 테스트(anyio_backend의 loop_factory), CLI(`asyncio.run(loop_factory=...)`) 모두 SelectorEventLoop를 쓴다.
- 2026-11-16 Alembic env.py는 동기 엔진으로 마이그레이션한다. 같은 psycopg 3 드라이버이고, 비동기 템플릿보다 코드가 짧고 Windows 루프 문제가 없다.
- 2026-11-16 비밀번호 해시는 bcrypt 5.0을 쓴다. argon2-cffi의 DLL이 Smart App Control에 막혔고, 명세가 "argon2 또는 bcrypt"를 허용한다. bcrypt는 72바이트까지만 쓰므로 넘는 비밀번호는 create-user가 거부하고 로그인은 실패로 처리한다.
- 2026-11-16 없는 이메일로 로그인해도 더미 해시를 한 번 검사해 응답 시간으로 계정 존재 여부가 드러나지 않게 한다. 401 문구도 이메일·비밀번호 중 무엇이 틀렸는지 말하지 않는다.
- 2026-11-16 Origin 헤더가 없는 상태 변경 요청도 403이다. 명세의 "같아야 한다"를 그대로 따른 것이고, 브라우저는 POST에 항상 Origin을 붙인다. curl로 시험할 때는 `-H "Origin: http://localhost:3000"`을 붙인다.
- 2026-11-16 Origin 검사는 라우터별 의존성이 아니라 미들웨어로 앱 전체에 건다. 새 라우터를 만들 때 빠뜨릴 수 없게 하기 위해서다.
- 2026-11-16 남의 대화는 403이 아니라 404로 답한다. 403이면 그 id의 대화가 존재한다는 사실이 드러난다.
- 2026-11-16 서명 쿠키 세션은 서버에서 개별 세션을 강제로 끊을 수 없다. 필요하면 `SESSION_SECRET`을 바꿔 전체를 로그아웃시킨다. 유효기간은 14일.
- 2026-11-16 상태 값은 PostgreSQL ENUM 대신 VARCHAR + CHECK 제약(SQLAlchemy Enum, native_enum=False)으로 저장한다. 값을 추가할 때 마이그레이션이 단순하다.
- 2026-11-16 `approvals.expires_at`(생성 + 30분), `watches.expires_at`(생성 + 30일)은 DB 기본값으로 넣고, `watches.interval_minutes >= 30`은 CHECK 제약으로도 막는다.
- 2026-11-16 `tasks.trigger_message_id`는 null을 허용한다(UNIQUE). 예약·감시 작업이 메시지 없이 시작될 수 있어서다. 6단계에서 다시 확인한다.
- 2026-11-16 notes, todos에는 명세에 user_id가 적혀 있지 않지만 "모든 테이블에 user_id"(프로젝트 개요)에 따라 넣었다. messages, steps 등 상위 테이블을 거쳐 소유자를 알 수 있는 테이블에는 넣지 않았다(명세의 컬럼 목록대로).
- 2026-11-16 앱은 `create_app(settings)` 팩토리로 만든다. 테스트가 실제 `.env`(키 포함)를 읽지 않고 테스트 설정으로 앱을 만들 수 있다.
- 2026-11-16 테스트 DB는 docker postgres 안의 `myagent_test`를 매 실행마다 지우고 Alembic으로 다시 만든다. 테스트마다 트랜잭션을 열고 세션은 SAVEPOINT로 묶어(join_transaction_mode="create_savepoint") 끝나면 되돌린다. DB 이름이 `_test`로 끝나지 않으면 지우지 않는다.
- 2026-11-16 Python 도구는 `uv run python -m 도구`로 실행한다. uv가 만드는 `.venv\Scripts\*.exe` 런처가 Smart App Control에 막힌다(ruff.exe는 서명돼 있어 예외).
- 2026-11-16 `.env` 값은 작은따옴표로 감싼다. docker compose가 루트 `.env`를 변수 치환용으로 읽으면서 `$`가 든 값을 변수로 해석해 일부를 경고로 출력한 일이 있었다.
- 2026-11-16 루트 `package.json`은 실행 스크립트 전용이고 pnpm 워크스페이스를 만들지 않는다. apps/web은 자체 pnpm-workspace.yaml을 가진 독립 프로젝트다. 여러 프로세스 동시 실행은 concurrently 10.0.6(배포 후 한 달 넘은 버전)으로 한다.
- 2026-11-16 API 서버는 127.0.0.1에만 연다. 폰은 Next.js(3000)의 rewrites를 거쳐서만 API에 닿는다.
