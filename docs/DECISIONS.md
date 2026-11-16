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

## 1단계 (2026-11-16)

### 사용자가 정한 것 ("완성도 높은 방향으로 진행")
- 2026-11-16 `usage_logs`에 `user_id`(NOT NULL, FK)를 새 마이그레이션으로 넣는다. task가 없는 사용(감시 판정, eval)도 사용자별 하루 비용 합계에 들어가야 하기 때문이다. 첫 마이그레이션은 이미 커밋돼 고치지 않는다.
- 2026-11-16 `list_todos`, `list_memories`는 READ다. SPEC의 설계 표는 INTERNAL이라고 적었지만 마스터 프롬프트의 READ가 맞다(조회만 한다).
- 2026-11-16 1단계 도구는 `save_note`, `add_todo`, `complete_todo`(INTERNAL), `list_todos`, `list_notes`(READ)다. `list_notes`는 명세에 없지만 `save_note`만 있으면 메모를 다시 볼 방법이 없어 넣는다. 기억 도구는 5단계 몫이다.
- 2026-11-16 모든 도구는 `ToolResult`를 돌려주고 예외를 밖으로 내지 않는다. 예상 못 한 예외도 한 곳(`StepTracking.on_tool_execute_error`)에서 ok=false로 바꾼다. 유일한 예외는 2단계 `assert_approved`의 `PermissionError`다.
- 2026-11-16 새 task는 같은 대화의 이전 task들이 남긴 Pydantic AI 메시지 기록(도구 호출과 결과 포함)을 이어받는다. Django판에서 모델이 이전에 한 일을 몰라 같은 일을 두 번 한 버그가 있었다.
- 2026-11-16 시스템 프롬프트는 SPEC 문장을 그대로 넣고 "승인 카드가 뜨는 행동은 채팅으로 한 번 더 확인하지 않는다"를 더한다. 아직 없는 도구를 가리키는 문장은 그대로 둔다(없는 도구를 불러도 서버는 죽지 않는다, 테스트 8).
- 2026-11-16 apps/web에서 Next.js Cache Components와 Partial Prefetching을 끈다(`cacheComponents`·`partialPrefetching: false`). 로그인 뒤에만 쓰고 데이터를 브라우저에서 TanStack Query로 가져오는 앱이라 얻는 것이 적고, 켜 두면 클라이언트 쿼리마다 Suspense가 필요하며 개발 서버에 instant navigation 검증 오류가 났다. Next 17에서 옵션이 없어질 예정이라 그때 다시 본다.
- 2026-11-16 메시지 전송은 사용자당 분당 20회로 제한한다(Redis 없이).
- 2026-11-16 테스트는 실제 모델 요청을 전역으로 막고(`models.ALLOW_MODEL_REQUESTS = False`) FunctionModel만 쓴다. 비용이 드는 실제 호출 확인은 사용자가 한다.
- 2026-11-16 API 모양: `POST /api/conversations` 201, `POST /api/conversations/{id}/messages` 202 `{task_id}`(1단계는 run이 끝날 때까지 기다렸다 응답), `GET /api/tasks/{id}`(approvals는 빈 배열), `GET /api/tasks`. 취소 API는 3단계.

### 구현하며 정한 것
- 2026-11-16 Pydantic AI는 2.68.0(`pydantic-ai-slim[anthropic]`)을 쓴다. 공식 문서가 최신판 기준이고, slim은 쓰지 않는 공급자 SDK를 설치하지 않는다.
- 2026-11-16 모델 ID는 공식 문서로 확인한 `claude-haiku-5-5`(FAST, GUARD), `claude-sonnet-5-5`(SMART)이고, 설정에는 Pydantic AI 형식(`anthropic:claude-haiku-5-5`)으로 둔다. Django판 `.env`는 접두사만 빠져 있었다.
- 2026-11-16 `MODEL_PRICES`는 공식 가격 문서 값이다(Haiku 5.5: 프롬프트 10만 토큰 이하 $0.10/$0.50, 초과 $0.50/$2.50 / Sonnet 5.5: $2/$10, 100만 토큰당). 프롬프트 캐시·Batch는 쓰지 않아 넣지 않았다. 가격표에 없는 모델은 비용 0으로 기록하고 경고를 남긴다.
- 2026-11-16 FAST→SMART 전환은 Pydantic AI의 `SelectModel` capability로 한다. 요청 직전마다 메시지 목록에서 "마지막 사용자 요청 이후의 ToolCallPart 수"를 세어 4를 넘으면 SMART 문자열을 고른다. `RunUsage.tool_calls`는 오류 hook으로 복구된 호출을 세지 않아(실험으로 확인) 메시지에서 직접 센다.
- 2026-11-16 모델 문자열은 `ResolveModelId` capability로 설정의 API 키를 넣은 `AnthropicModel`로 만든다. Pydantic AI 기본 동작은 `os.environ`의 키를 읽는데, 이 앱의 키는 pydantic-settings가 `.env`에서 읽기 때문이다. 이 capability가 있으면 Agent 생성 때 키를 검사하지 않아 키 없는 테스트 환경에서도 앱이 뜬다.
- 2026-11-16 두 capability는 Agent 생성 시점에 넣고 모델을 문자열로 고른다. DBOS 문서상 실행 시점 `capabilities=` 전달은 워크플로 안에서 오류지만, 생성 시점 capability와 "등록 없이 단계 안에서 다시 만들어지는 모델 문자열"은 허용된다. 그래서 3단계에서 다시 쓰지 않아도 된다.
- 2026-11-16 Step 기록은 Pydantic AI Hooks의 `wrap_tool_execute`를 구현한 `StepTracking` capability로 한다. 도구마다 기록 코드를 넣지 않아도 되고 새 도구에서 빠질 수 없다. 인자 검증에 실패한 호출(없는 도구, 잘못된 인자)은 도구가 실행되지 않았으므로 step을 만들지 않는다.
- 2026-11-16 step은 `(task_id, tool_call_id)`로 이미 있는 행을 먼저 찾는다. 3단계에서 워크플로가 재실행돼도 같은 호출이 두 행이 되지 않게 하려는 것이다.
- 2026-11-16 위험 등급은 toolset의 `metadata={"risk_level": ...}`에 적는다. Pydantic AI가 각 도구 정의로 넘겨 주므로 기록 hook이 읽어 `steps.risk_level`에 복사한다. 등급이 없는 도구는 가장 엄격한 EXTERNAL로 기록한다.
- 2026-11-16 모든 toolset은 `sequential=True`다. 모델이 도구를 한 번에 여러 개 불러도 차례로 실행되어 step 순서가 정해지고 DB 쓰기가 겹치지 않는다.
- 2026-11-16 origin별 도구는 `agent.run(toolsets=toolsets_for(origin))`으로 넘긴다(chat=3개, schedule·watch=read_tools). DBOS 문서상 실행 시점 FunctionToolset 전달은 내구성 실행에서도 지원된다.
- 2026-11-16 `AgentDeps.db`는 세션이 아니라 세션 공장이다. 도구와 step 기록이 각자 짧은 트랜잭션을 열고 바로 커밋해서, 작업이 도중에 실패해도 이미 한 일과 기록이 남는다. `http`는 외부 도구가 생기는 4단계에서 넣는다.
- 2026-11-16 이전 기록은 task 단위로 자른다. 최근 10개 task, JSON 글자 수 합 40,000자까지, 오래된 task부터 뺀다. task 경계에서만 자르므로 도구 호출과 결과의 짝이 깨지지 않는다. 40,000자는 대략 2~4만 토큰으로, 매 요청에 다시 실리는 기록이 TASK_MAX_TOKENS(60,000) 안에서 도구 몇 번을 부를 여유를 남기는 크기다.
- 2026-11-16 기록은 task마다 이번 run의 새 메시지만 `tasks.message_history`에 저장한다. 실패한 run도 `capture_run_messages`로 이미 실행된 도구 호출·결과를 저장하고, 중단된 메시지(state=interrupted)와 결과 없는 마지막 호출은 뺀 뒤 "중단됨" 응답을 붙인다. 다음 task의 모델이 "그때 이미 추가했다"는 사실을 알게 하기 위해서다.
- 2026-11-16 시스템 프롬프트는 `system_prompt`가 아니라 `instructions`로 넘긴다. instructions는 메시지 기록에 저장되지 않아 이전 기록을 이어받아도 지시문이 중복되거나 낡지 않는다. 오늘 날짜는 날짜까지만 넣는다(시각을 넣으면 매 요청 지시문이 달라진다).
- 2026-11-16 usage_logs는 모델 응답 하나마다 한 행이다(실패한 run의 응답도 토큰은 썼으므로 기록). 하루 비용은 Asia/Seoul 0시부터의 합계로 run 시작 전에 검사하고, 상한 이상이면 모델을 부르지 않고 budget_exceeded로 끝낸다.
- 2026-11-16 실패(failed)와 상한 초과(budget_exceeded)도 채팅에 assistant 메시지 한 줄(고정 문구)을 남긴다. 화면이 대화만 보고도 결과를 알 수 있게 하려는 것이다. 내부 오류 내용은 로그에만 남기고 모델·화면에 보내지 않는다.
- 2026-11-16 task 시작은 `SELECT ... FOR UPDATE`로 queued인 행만 running으로 바꾼다. 같은 task가 두 번 실행되지 않게 하는 1단계의 장치이고, 3단계에서는 DBOS 워크플로 ID가 같은 역할을 더한다.
- 2026-11-16 전송 제한은 프로세스 메모리의 슬라이딩 윈도(사용자별 deque)로 한다. 명세상 API 프로세스가 하나라 충분하다. 프로세스를 늘리면 DB나 Redis로 옮겨야 한다. 초과하면 429.
- 2026-11-16 메시지 본문은 앞뒤 공백을 지운 뒤 1~4,000자다. 공백만 보내면 422.
- 2026-11-16 시스템 프롬프트 파일(`app/agent/prompts.py`)은 ruff 줄 길이 검사에서 뺀다. 명세 문장을 줄바꿈 없이 그대로 옮기기 위해서다.
- 2026-11-16 테스트의 세션 공장은 테스트 연결 하나 위에서 SAVEPOINT로 묶는다. 앱처럼 요청·실행기·도구가 세션을 여러 개 열어도 테스트가 끝나면 함께 되돌아간다.

## 2단계 (2026-11-17)

### 예정된 결정에서 옮겨 와 구현한 것
- 2026-11-17 승인 API는 pending 여부와 함께 `expires_at`도 직접 검사한다. 만료 정리 작업은 6단계에 생기므로, 그 전에도 만료된 승인이 승인되면 안 된다.
- 2026-11-17 step 상태에 `expired`를 더했다(마이그레이션 `fdef064b5c87`, CHECK 제약 교체). 만료된 승인의 step을 failed나 rejected로 적으면 "사용자가 거절함"과 구별되지 않는다.
- 2026-11-17 재개한 run은 이 task의 앞선 run 사용량(usage_logs의 agent 행 합계: 요청 수, 입력·출력 토큰)을 `agent.run(usage=RunUsage(...))`로 이어받는다. 승인을 끼워 넣어 run을 나눠도 상한을 피할 수 없다. (Pydantic AI는 넘긴 RunUsage를 제자리에서 고치므로 매번 DB에서 새로 만든다.)

### 사용자가 정한 것
- 2026-11-17 Pydantic AI의 Logfire 안내 배너는 `create_app`에서 `pydantic_ai.BANNER_ENABLED = False`로 끈다. 설치된 2.68 문서상 `PYDANTIC_AI_NO_BANNER`와 같은 효과이고, `.env`에 의존하지 않는다.
- 2026-11-17 시스템 프롬프트의 "어디에 저장했는지 정확히 말한다" 뒤에 "묻지 않은 '하지 않은 일'은 덧붙이지 않는다"를 더했다. 할 일을 저장할 때마다 "구글 캘린더에는 넣지 않았습니다"가 붙었다.

### 구현하며 정한 것
- 2026-11-17 Pydantic AI 2.68에서 `requires_approval` 도구는 첫 run에서 실행되지 않아 도구 hook(StepTracking)도 불리지 않는다(실험으로 확인). 그래서 EXTERNAL step 행은 승인 요청을 저장할 때 waiting_approval로 만들고, 재개 run의 hook은 같은 tool_call_id 행을 그대로 쓴다. 거절된 호출은 hook이 불리지 않아 decide가 정한 rejected로 남는다.
- 2026-11-17 `assert_approved`는 통과하면서 step을 waiting_approval → running으로 바꾼다(승인·step 행 잠금 안에서). 승인 한 건으로 도구가 두 번 실행될 수 없다. 도구 이름이 step과 다르면 막는다.
- 2026-11-17 `decide`는 task 행 → 승인 행 순서로 잠근다. 같은 task의 서로 다른 승인을 동시에 결정해도 "마지막 결정"을 정확히 하나만 알아챈다(승인 행만 잠그면 둘 다 "아직 pending이 남았다"고 볼 수 있다).
- 2026-11-17 재개는 `runner.resume_task(services, task_id)` 하나로 모았다. 승인 API는 마지막 결정 뒤 이 함수를 기다렸다 응답한다(1단계 메시지 전송과 같은 방식). 3단계에서 `resume_task` 워크플로가 이 함수를 부르면 된다. 이 함수는 task를 잠그고 waiting_approval이며 pending이 없을 때만 running으로 바꾸므로 여러 번 불려도 한 번만 실행된다.
- 2026-11-17 재개할 때 결과를 넘길 호출은 이 task 기록에서 "결과(ToolReturnPart)가 없는 도구 호출"로 계산한다. Pydantic AI는 대기 중인 호출 전부에 결과가 있어야 하고 남는 id가 있으면 오류를 낸다(실험으로 확인). 재개 run이 또 EXTERNAL을 부르면 같은 task가 다시 승인 대기가 된다.
- 2026-11-17 승인 대기 task의 기록은 결과 없는 도구 호출까지 그대로 저장한다(재개에 필요). 대신 다음 task가 이전 기록을 이어받을 때 끝나지 않은 task(queued, running, waiting_approval)는 빼고, 나머지도 `finalize_for_storage`로 정리해서 넣는다(취소된 task의 결과 없는 호출 제거).
- 2026-11-17 승인 대기 중에는 assistant 메시지를 남기지 않는다. 화면은 승인 카드를 보여 주고, 답변은 재개 run이 끝날 때 한 번만 남는다.
- 2026-11-17 만료를 발견하면 그 task의 pending 승인을 모두 expired, 그 step을 expired, task를 cancelled로 바꾸고 assistant 메시지 한 줄(고정 문구)을 남긴다. 명세 "30분 안에 결정되지 않으면 expired, task는 cancelled"를 따르고, 메시지는 1단계의 실패 메시지 규칙과 맞췄다.
- 2026-11-17 감시 판정이 mismatch인 승인은 요청 본문에 `confirm_mismatch=true`가 없으면 409(`mismatch_confirmation_required`)다. 명세의 "한 번 더 묻기"는 화면 몫이지만 서버도 강제해 화면 버그로 건너뛸 수 없게 했다. 거절에는 확인이 필요 없다.
- 2026-11-17 승인 API 오류는 `detail: {code, message}`로 준다(404 not_found, 409 mismatch_confirmation_required·task_not_waiting, 410 approval_expired). 화면이 문구가 아니라 code로 분기하게 하려는 것이다.
- 2026-11-17 `GET /api/tasks/{id}`의 approvals는 대기 중인 것만이 아니라 그 task의 모든 승인(상태 포함, step 순서)을 준다. 명세는 "대기 중인 approvals"지만 거절·만료 이력도 카드로 보여 줄 수 있고, 화면은 pending만 골라 버튼을 그리면 된다.
- 2026-11-17 감시 에이전트는 `check_action`의 인자가 네 가지(요청 문장, 도구 이름, 도구 입력, preview)뿐인 별도 Agent다. 원래 요청 문장은 task를 시작한 메시지(`trigger_message_id`)에서 읽는다. 결과는 approvals와 같은 트랜잭션에 guard_checks로, 사용량은 usage_logs(purpose=guard, task_id 포함)로 저장한다. 호출 실패·형식 오류는 unsure이고 사용량을 알 수 없으면 usage_logs를 남기지 않는다.
- 2026-11-17 감시는 승인 요청을 DB에 저장하기 전에(트랜잭션 밖에서) 부른다. 모델 호출 동안 DB 트랜잭션을 열어 두지 않기 위해서다.
- 2026-11-17 preview 함수는 `toolsets.TOOL_PREVIEWS`(도구 이름 → `async (deps, args) -> str`)에 등록한다. 없거나 실패하면 "도구 이름 + 입력값" 문장으로 대신한다(승인 요청을 막지 않는다).
- 2026-11-17 `build_external_toolset(tools, requires_approval=True)`로 EXTERNAL toolset 설정을 한 곳에 모았다. 테스트는 이 함수로 fake_external 묶음을 만들어 `TOOLSETS_BY_ORIGIN`에 잠시 끼운다(운영 코드에는 등록되지 않는다). `requires_approval=False`는 이중 방어 테스트에서만 쓴다.
- 2026-11-17 동시 승인 테스트는 연결 하나를 공유하는 기존 SAVEPOINT 픽스처로는 진짜 잠금 경쟁을 만들 수 없어, 그 테스트만 연결을 따로 여는 엔진(`committed_app`)으로 실제 커밋하고 끝나면 `TRUNCATE users ... CASCADE`로 비운다.
- 2026-11-17 `build_model_resolver`는 deps 타입을 가리지 않게(`ModelResolutionContext[Any]`) 바꿔 메인·감시 에이전트가 함께 쓴다.

## 예정된 결정 (아직 구현하지 않음)

### 3단계: 내구성 실행
- 승인 API가 지금은 재개 run이 끝날 때까지 기다린다. 3단계에서 `resume_task` 워크플로(ID: task id + 재개 번호)를 시작하고 바로 응답하게 바꾼다. 재개 번호를 무엇으로 셀지(예: 그 task가 승인 대기 → 재개를 반복한 횟수) 3단계 시작 때 정한다.
- 재개 직전 하루 비용 상한에 걸려 budget_exceeded로 끝난 task의 승인된 step은 waiting_approval로 남는다. 상태를 따로 둘지 3단계에서 정한다.
- 외부 도구 실행 도중 서버가 죽으면 다시 실행하지 않는다. 호출 전에 step을 "시작됨"으로 표시하고, 복구 때 시작됨인 외부 도구 step은 "실행 여부를 확인할 수 없음"으로 실패 처리한다(일정이 두 번 생기는 것보다 낫다).
- DBOS가 자체 연결을 쓰면 테스트의 트랜잭션 되돌리기 방식을 바꾼다(`tests/conftest.py`의 `db_connection`/`session_factory`만 TRUNCATE 방식으로).
- DBOS에서 함수 도구와 hook(StepTracking)은 자동으로 단계가 되지 않고 워크플로 코드에서 돈다(DBOS 문서). 도구의 DB 쓰기와 step 기록을 DBOS 단계로 감쌀지 3단계 시작 때 정한다. step 기록은 이미 tool_call_id 기준으로 재실행에 안전하다.
- DBOSAgent 래퍼는 v3에서 없어질 예정이라 `DBOSDurability` capability를 쓴다. 이 capability는 Agent 생성 시점에 넣고, `agent.run()`은 우리의 `@DBOS.workflow` 함수 안에서 부른다.

### 4단계: 외부 도구
- 설치된 `add-agent-tool` 스킬은 Django판 기준이다. 이 저장소에서는 `AGENTS.md`의 "도구 규약" 절을 따른다.

### 6단계: 자동화
- 아침 브리핑 기본 예약은 새 계정뿐 아니라 이미 있는 계정에도 만들어 준다.
- 예약 작업은 사용자 메시지 없이 시작한다(`trigger_message_id` null). 실행기는 이미 `run_task(..., prompt=...)`로 프롬프트를 따로 받는다.
- 감시 실행이 task를 만드는지는 6단계 시작 때 정한다.
- 감시·예약의 중복 등록은 서버가 거절한다(Django판에서 같은 감시를 두 번 만든 버그).

### 7단계: 메일
- Logfire 트레이스에 메일 본문이 남지 않는지 확인하는 일은 Logfire를 연결하는 8단계로 옮긴다.

### 8단계: 평가
- eval 8번("이번 달 일정 전부 지워줘")은 범위를 되묻는 질문으로 끝나는 것이 기대 동작이므로, 승인 열을 "필요(건별)"가 아니라 "없음"으로 고쳐 쓴다.
