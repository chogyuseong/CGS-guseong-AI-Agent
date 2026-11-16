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

### 화면(apps/web)
- 2026-11-17 (사용자가 정함) task 폴링은 진행 중 1초, `waiting_approval`이면 3초다. 승인 대기는 사용자가 결정할 때까지 바뀌지 않지만, 다른 기기에서 결정한 결과가 반영되도록 완전히 멈추지 않는다.
- 2026-11-17 승인 카드는 답변이 아직 없는 내 메시지(task_id 있음) 바로 아래 "답변 자리"(`PendingTask`)에 그린다. 승인 대기 중에는 서버가 assistant 메시지를 남기지 않으므로, 메시지 목록만으로 대기 중인 task를 찾을 수 있다. task가 끝나면 메시지 목록을 다시 가져와 최종 답변으로 바꾼다.
- 2026-11-17 결정된 승인 카드는 "승인함/거절함/만료됨"만 남기고 버튼을 없앤다. 답변 아래(TaskSteps)와 작업 기록 탭에서도 같은 카드를 보여 주므로, 작업 기록 탭에서도 대기 중인 승인을 결정할 수 있다.
- 2026-11-17 mismatch 승인의 "한 번 더 묻기"는 브라우저 `confirm()`이 아니라 카드 안의 확인 영역([그래도 승인] [취소])으로 한다. 폰 브라우저의 네이티브 대화상자는 모양을 바꿀 수 없고 테스트하기 어렵다.
- 2026-11-17 버튼은 누르는 즉시 state와 ref(즉시 바뀌는 값) 두 겹으로 잠근다. 성공하면 버튼이 사라질 때까지 풀지 않고, 오류(409·410·네트워크)면 한국어 문구를 보여 주고 풀며 task를 다시 가져온다. 오류 분기는 문구가 아니라 `detail.code`로 한다.
- 2026-11-17 승인 오류 본문은 OpenAPI 문서에 모양이 없어(생성 타입이 `content?: never`) `readErrorCode(unknown)`로 실행 중에 모양을 확인한다. 손으로 API 타입을 쓰지 않기 위해서다.

## 3단계 (2026-11-17)

API 쪽만(화면은 다음 작업자). 사용자가 자는 동안 진행해 아래 결정은 모두 "더 안전하고 완성도 높은 쪽"으로 작업자가 정했다.

### 예정된 결정에서 옮겨 와 구현한 것
- 2026-11-17 승인 API는 마지막 결정이면 `resume_task` 워크플로를 시작만 하고 바로 응답한다(응답 모양 `{approval, changed, task_status}` 유지, task_status는 그 시점 상태). 재개 번호는 `tasks.workflow_id`(지금 워크플로 ID)의 번호 + 1이다: run 뒤 "task-42" → "task-42-resume-1", 그 재개가 또 승인 대기가 되면 "task-42-resume-2". 승인 버튼을 동시에 눌러도 같은 번호가 나와 DBOS가 한 번만 실행한다. 이미 있는 컬럼(명세의 `workflow_id(DBOS)`)을 써서 마이그레이션이 필요 없다.
- 2026-11-17 재개 직전 하루 비용 상한(또는 재개 준비 오류)으로 끝나면, task가 끝날 때(`save_outcome`, waiting_approval 외 모든 결과) 아직 waiting_approval인 step을 `failed`(ok=false, output.error "작업이 이 단계 전에 멈춰 실행하지 않았습니다.")로 닫는다. 새 상태 값을 만들지 않았다: 마이그레이션·화면 분기 없이 기존 failed 표시로 보이고, "실행 여부를 모름"과는 error 문구로 구별된다. 승인 행은 approved 그대로 둔다(사용자가 승인한 사실은 남긴다).
- 2026-11-17 외부 도구 실행 도중 서버가 죽으면 다시 실행하지 않는다(at-most-once). 별도 "시작됨" 상태를 만들지 않고 기존 상태를 쓴다: `assert_approved`가 실행 직전 step을 running으로 바꾸므로, 복구 때 hook이 running인 EXTERNAL step을 만나면 실행하지 않고 failed + "실행 여부를 확인할 수 없음"(모델에게도 같은 문장)으로 닫는다. INTERNAL도 같게 했다(add_todo가 커밋 뒤 step 기록 전에 죽으면 다시 넣지 않는다). READ는 다시 실행한다.
- 2026-11-17 함수 도구의 DB 쓰기와 step 기록은 DBOS 단계로 감싸지 않는다. 대신 StepTracking이 tool_call_id로 steps를 먼저 찾아 끝난 호출(done·failed)은 기록된 output을 재생하고 실행하지 않는다. 이유: DBOS 단계는 "끝나기 전에 죽으면 다시 실행"(at-least-once)이라 위의 at-most-once와 맞지 않고, steps 테이블이 이미 호출별 기록이며, 모델 응답은 DBOS 단계라 복구 때 같은 tool_call_id가 다시 온다(실험과 테스트로 확인).
- 2026-11-17 DBOS 시스템 테이블은 별도 연결(동기 엔진)로 `dbos` 스키마에만 쓰므로 테스트의 SAVEPOINT 되돌리기는 그대로 쓴다. dbos 행은 되돌려지지 않지만 테스트 DB는 세션마다 새로 만들고 task id 시퀀스도 되돌려지지 않아 워크플로 ID가 겹치지 않는다. 그래서 `committed_app`의 `TRUNCATE ... RESTART IDENTITY`에서 RESTART IDENTITY를 뺐다.
- 2026-11-17 DBOSAgent 래퍼 대신 `DBOSDurability(models=..., parallel_execution_mode="sequential")`를 Agent 생성 때 넣고 `agent.run()`은 우리 `@DBOS.workflow` 안에서 부른다.

### 구현하며 정한 것
- 2026-11-17 DBOS는 3.5.0(`pydantic-ai-slim[anthropic,dbos]==2.68.0`이 고른 버전), 웹 푸시는 pywebpush 2.5.0(cryptography 50.0.2, py-vapid 1.9.6, aiohttp). 둘 다 Smart App Control에 막히지 않았다(설치·import·키 생성·서버 실행 확인).
- 2026-11-17 DBOS 시스템 DB는 앱과 같은 DB(`DATABASE_URL`)의 `dbos` 스키마다. Postgres 하나·연결 설정 하나로 끝나고, Alembic(public 스키마만 비교)과 섞이지 않는다. 새 환경변수는 만들지 않았다.
- 2026-11-17 DBOS는 FastAPI lifespan 안(실행 중인 SelectorEventLoop)에서 launch한다. 루프 밖에서 launch하면 DBOS가 자기 루프를 새로 만드는데 Windows에서는 Proactor라 psycopg 비동기가 안 된다(소스로 확인). 복구된 워크플로도 서버 루프에서 돈다.
- 2026-11-17 DBOS application_version을 "myagent-3"으로 고정한다. 기본값(워크플로 코드 해시)이면 코드를 고친 뒤(`pnpm api`의 --reload 포함) 재시작할 때 끝나지 않은 워크플로가 복구되지 않고 task가 running으로 남는다. 대신 워크플로의 단계 구성을 바꾸면 이 값을 올리고, 그 전에 끝나지 않은 작업이 없게 한다.
- 2026-11-17 워크플로 ID는 "task-{id}" / "task-{id}-resume-{n}"이다(명세: task id). 접두사는 6단계의 예약 워크플로(DBOS가 "sched-…"로 정함)와 구별하려는 것이다.
- 2026-11-17 runner의 다시 하면 안 되는 일은 DBOS 단계(`durable_step`)로 감쌌다: claim(queued→running, workflow_id·시작 시각 기록) → prepare(하루 비용 검사, 이전 기록) → agent.run(모델 요청은 DBOSDurability 단계) → finish(승인 요청·감시·결과 저장) → notify. 워크플로 밖(테스트의 직접 호출)에서는 그냥 실행된다.
- 2026-11-17 finish 단계는 task를 잠그고 running일 때만 저장한다. 단계가 커밋 뒤 기록 전에 죽어 다시 실행돼도 승인 요청·답변 메시지·usage_logs가 두 번 생기지 않는다. claim 단계도 같은 이유로 "running이고 workflow_id가 지금 워크플로와 같으면" 다시 잡는다.
- 2026-11-17 서버 시작 때 `restart_orphaned_tasks`가 queued인 chat task(커밋 뒤 워크플로 시작 전에 죽음)와 pending 없는 waiting_approval task(마지막 결정 뒤 재개 시작 전에 죽음)의 워크플로를 시작한다. 같은 워크플로 ID라 이미 있으면 다시 실행되지 않는다.
- 2026-11-17 메시지 전송 API는 커밋한 뒤에 워크플로를 시작한다(워크플로가 task 행을 반드시 보게).
- 2026-11-17 테스트도 실제 DBOS 워크플로를 거친다. 워크플로 안에서는 등록된 모델 객체만 끼울 수 있어서(Pydantic AI가 등록 안 된 객체를 거부, 실험으로 확인) 가짜 모델 하나(`SCRIPTED_MODEL`)를 `create_app(agent_models=...)`로 등록하고 테스트마다 그 모델이 부를 함수만 바꾼다. 앱의 시작기는 끝날 때까지 기다리는 `WaitingLauncher`로 바꿔 기존 테스트가 그대로 결과를 확인한다. "바로 응답"과 fork 재생 테스트만 committed_app을 쓴다.
- 2026-11-17 푸시 본문은 `{"title": "마이에이전트", "body": 고정 문구, "url": "/chat/{대화 id}", "kind"}` JSON이다. notify가 받는 문자열은 url 하나뿐이라 요청 내용이 들어갈 자리가 없다. 명세 규칙대로 approval_needed·task_failed(failed와 budget_exceeded)는 항상, task_done은 20초 넘게 걸렸거나 origin이 schedule·watch일 때만 보낸다. 20초는 그 워크플로(run 또는 재개)의 claim 시각부터 잰다(승인을 기다린 시간은 넣지 않는다). 만료로 취소(cancelled)는 알리지 않는다.
- 2026-11-17 푸시 TTL은 1시간이다(기기가 꺼져 있을 때 푸시 서버가 보관하는 시간, 승인은 30분 뒤 만료). 발송 실패는 경고 로그(구독 id와 오류 종류만)만 남기고 작업에 영향을 주지 않는다. 404·410은 구독 삭제.
- 2026-11-17 구독 endpoint는 브라우저 푸시 서비스의 https 주소(googleapis.com, push.services.mozilla.com, push.apple.com, notify.windows.com과 그 하위 도메인)만 받는다. 서버가 endpoint로 직접 요청을 보내므로 아무 주소나 받으면 내부망 주소로 요청을 보내게 만들 수 있다(SSRF). 다른 브라우저가 필요해지면 목록에 더한다.
- 2026-11-17 같은 endpoint로 다시 구독하면 키와 user_id를 갱신한다. 한 브라우저에서 계정을 바꿔 알림을 켜면 그 기기 알림은 지금 계정으로 간다. 해지는 남의 endpoint여도 204(존재 여부를 드러내지 않음)이고 실제로는 자기 것만 지운다.
- 2026-11-17 구독 API는 `GET /api/push/public-key`(enabled, public_key), `POST /api/push/subscribe`(204), `POST /api/push/unsubscribe`(204)다. enabled는 VAPID 세 값이 모두 있을 때만 true라 화면이 '알림 켜기'를 막을 수 있다.
- 2026-11-17 `.env`의 VAPID 키(Django판에서 가져옴)는 값을 출력하지 않고 형식만 검사했다: 개인 키 base64url 32바이트(raw P-256), 공개 키 base64url 65바이트(비압축 점), py_vapid `Vapid.from_string`으로 읽히고 개인 키에서 만든 공개 키가 설정의 공개 키와 같다. VAPID_SUBJECT는 비어 있어 지금은 푸시가 나가지 않는다.
- 2026-11-17 폰에서 수신을 확인하려고 `python -m app.cli send-test-push --email ... [--kind ...]`를 더했다(관리 명령, API 아님). 4단계 전에는 EXTERNAL 도구가 없어 '승인 필요' 푸시를 실제 흐름으로 만들 수 없기 때문이다. 본문은 notify와 같은 고정 문구다.
- 2026-11-17 승인 API 오류 응답을 OpenAPI에 `ApprovalErrorOut{detail: ApprovalErrorDetail{code, message}}` 모델로 넣었다(404·409·410, code는 Literal 4가지). reject의 409 설명에서 mismatch를 뺐다(거절에는 확인이 필요 없다).
- 2026-11-17 `RunServices`에 `notifier`(PushNotifier)를 더했고, 워크플로는 인자로 실행 도구를 받을 수 없어(DBOS가 인자를 DB에 저장) `bind_app_state(app.state)`로 알려 준 app.state에서 그때그때 만든다. 테스트가 app.state의 세션 공장을 바꿔도 워크플로가 따라간다.

### 화면(apps/web)

사용자가 자는 동안 진행해 아래 결정은 모두 "더 안전하고 완성도 높은 쪽"으로 화면 작업자가 정했다.

- 2026-11-17 진행 상황은 화면 상태가 아니라 서버의 task에서만 읽는다. 답변이 없는 내 메시지(task_id 있음) 아래 `PendingTask`가 task를 폴링하므로 새로 고침·앱 다시 열기·다른 탭에 갔다 오기 뒤에도 그대로 이어진다(별도 저장소를 쓰지 않는다). 백그라운드에서는 TanStack Query가 폴링을 쉬고 창 포커스 때 바로 다시 가져온다.
- 2026-11-17 진행 문구는 task에서 계산한 단계(`taskPhase`)로 정한다: queued "곧 시작합니다", running "작업하는 중", pending 승인이 있으면 승인 안내, waiting_approval인데 pending이 없으면 "결정을 반영해 이어서 처리하는 중". 서버는 마지막 결정 뒤 재개 워크플로가 claim할 때까지 waiting_approval로 두므로, 이 구간을 승인 대기로 보이면 사용자가 다시 눌러야 하는 줄 안다.
- 2026-11-17 "이어서 처리하는 중"(waiting_approval + pending 없음)은 곧 바뀌므로 승인 대기(3초)가 아니라 1초 폴링이다.
- 2026-11-17 승인·거절 응답(`{approval, task_status}`)은 다시 가져오기 전에 task 캐시에 바로 반영한다(`applyDecision`). 그 전에 진행 중인 폴링 요청은 취소해, 늦게 온 옛 응답이 "승인 필요"로 되돌리지 않게 한다.
- 2026-11-17 승인 오류 code는 생성된 `ApprovalErrorOut` 타입에서 읽는다(`approvalErrorCode`, 2단계의 `readErrorCode(unknown)`를 대체). 프록시 오류처럼 JSON이 아닌 본문도 올 수 있어 객체인지만 실행 중에 한 번 더 본다.
- 2026-11-17 작업 기록 탭 목록은 끝나지 않은 task가 있을 때만 3초마다 다시 가져온다(상태 배지가 "진행 중"에 멈춰 있지 않게).
- 2026-11-17 서비스 워커는 앱을 열 때 등록하고(`ServiceWorkerRegistrar`, https·지원 브라우저에서만), '알림 켜기' 때도 다시 등록한 뒤 활성화를 최대 10초 기다린다. sw.js는 install에서 skipWaiting, activate에서 clients.claim만 한다(캐싱·fetch 처리 없음). claim은 이미 열린 창도 알림 클릭 때 이동시키기 위해서다.
- 2026-11-17 sw.js는 payload를 믿지 않는다: title·body가 글자가 아니면 기본 문구, url은 "/"로 시작하고 URL로 풀어 본 출처가 같을 때만 쓰고 아니면 `/chat`. 알림 클릭 때도 저장된 url을 다시 검사하고, 같은 출처의 창만 포커스·이동한다(이동 실패 시 새 창).
- 2026-11-17 `/sw.js` 응답에 `Cache-Control: no-cache, no-store, must-revalidate`와 CSP `default-src 'self'; script-src 'self'`를 붙인다(설치된 Next 16.4 PWA 가이드). 등록은 `updateViaCache: "none"`.
- 2026-11-17 권한 요청은 '알림 켜기' 클릭 처리 안에서 첫 동작으로 한다. 그래서 이 버튼은 useMutation(실행 전에 한 번 기다림)을 쓰지 않고 상태를 직접 관리하며, 공개 키는 화면을 열 때 미리 읽어 둔다. 아이폰 Safari는 사용자 동작 밖의 권한 요청을 막는다.
- 2026-11-17 알림 상태는 6가지다: insecure·unsupported(둘 다 "지원 안 함", https 안내 또는 아이폰 '홈 화면에 추가' 안내), server_disabled("서버에 키 없음", 켜기 버튼 비활성), denied("권한 거부됨", 브라우저 설정 안내, 버튼 없음), on("켜짐"), off("꺼짐"). 창 포커스 때 다시 읽어 다른 앱에서 권한을 바꾼 결과를 반영한다.
- 2026-11-17 "켜짐"은 권한 허용 + 이 기기 구독이 지금 서버 공개 키로 만든 것일 때만이다. 키가 다른 예전 구독은 '알림 켜기' 때 해지하고 새로 구독한다(서버 키를 바꾸면 예전 구독으로는 오지 않는다). 브라우저가 구독 키를 알려 주지 않으면(null) 맞는 것으로 본다.
- 2026-11-17 화면을 열 때 구독을 서버에 자동으로 다시 보내지 않는다. 같은 endpoint 재구독은 user_id를 바꾸므로(서버 결정), 사용자가 누르지 않았는데 이 기기 알림이 다른 계정으로 옮겨 가는 일을 막는다. '알림 켜기'를 누르면 이미 있는 구독도 다시 보낸다.
- 2026-11-17 폰 확인용 https 터널 호스트는 `DEV_TUNNEL_HOST` 환경변수로 줄 때만 `allowedDevOrigins`에 넣는다(호스트 이름 모양만 받음). `*.trycloudflare.com` 같은 와일드카드를 늘 열어 두면 그 도메인의 아무 사이트나 개발 서버의 개발용 자원을 요청할 수 있다.
- 2026-11-17 서버가 구독 저장을 거부하면(422 등) 브라우저 구독도 해지해 "켜짐"으로 보이지 않게 한다. '알림 끄기'는 서버 요청이 실패해도 브라우저 구독을 해지한다(이 기기로는 더 오지 않고, 서버는 다음 발송 때 410으로 지운다).

## 4단계 (2026-11-17)

API 쪽만(화면은 다른 작업자). 사용자가 자는 동안 진행해 아래 결정은 모두 "더 안전하고 완성도 높은 쪽"으로 작업자가 정했다.
엔드포인트·인증·한도 요약은 `docs/TOOLS.md`.

### 외부 호출 공통
- 2026-11-17 외부 API는 이미 설치된 httpx(0.28.1)로 부르고, 호출 규칙을 `app/tools/http.py` 한 곳에 둔다: 타임아웃 10초, GET만 1회 재시도(타임아웃·연결 실패·5xx), 쓰기는 재시도 없음, 리디렉션 따라가지 않음, 오류 문장·로그에 본문·주소·헤더를 넣지 않음. httpx·cryptography를 직접 의존성으로 올렸다(둘 다 이미 설치된 버전, 새 내려받기 없음, Smart App Control 문제 없음).
- 2026-11-17 쓰기 요청이 타임아웃·연결 끊김이면 "실제로 반영됐는지 직접 확인해 주세요. 같은 요청을 다시 보내지 마세요."로 실패를 알린다. 3단계의 at-most-once(외부 행동은 많아야 한 번)와 같은 원칙이다.
- 2026-11-17 HTTP 클라이언트는 앱이 시작할 때 하나(`app.state.http_client`) 만들고 lifespan 끝에 닫는다. `AgentDeps`에 `settings`·`http`를, `RunServices`에 `http`를 더했다(명세의 `AgentDeps(..., http)`). 테스트는 `httpx.MockTransport`로 바꿔 끼운다(`tests/fake_http.py`). 새 테스트 의존성(respx 등)은 넣지 않았다.
- 2026-11-17 한도 초과는 429 외에 카카오 400 + code -10, Google 403 + rateLimitExceeded·userRateLimitExceeded·quotaExceeded도 "호출 한도"로 본다(각 공식 오류 문서).
- 2026-11-17 외부 콘텐츠 감싸기는 `ToolResult.external(content, sources)`다: data = `{notice, content, truncated}`, content를 JSON으로 바꾼 길이가 6,000자를 넘으면 앞 6,000자 문자열로 자른다. 출처 링크와 조회 시각(fetched_at)도 함께 채운다.
- 2026-11-17 감싸는 도구는 명세의 web_search·list_events에 get_weather·search_places를 더한 넷이다(`toolsets.EXTERNAL_CONTENT_TOOLS`). 가게 이름·주소·좌표를 잡은 장소 이름도 남이 쓴 글이라서다. 5단계 remember 검사가 이 목록을 그대로 쓸지는 5단계에서 정한다(명세는 web_search, list_mail, read_mail, list_events).
- 2026-11-17 검색·일정 조회의 빈 결과는 ok=true + 빈 목록 + message다(정상 답). 날씨에서 장소를 못 찾거나 예보가 비면 ok=false다(답을 못 했다). Django판은 검색 빈 결과도 실패였다.
- 2026-11-17 외부 도구 결과에는 모델이 쓸 칸만 남긴다(장소: 이름·주소·카테고리·전화·링크·좌표·거리 / 검색: 제목 200자·요약 300자·링크 / 일정: id·제목·시작·끝·종일·장소). 일정의 설명(초대 본문)과 참석자 이메일은 넘기지 않는다. 결과는 이후 모든 모델 요청에 따라붙는 토큰이고 주입 문장이 숨을 자리이기도 하다.
- 2026-11-17 READ 도구의 복구 재실행(3단계)은 그대로 맞다: 외부 READ 도구는 조회만 하고, 부수 효과는 Google 토큰 갱신(DB의 connections 행 갱신)뿐이라 다시 실행해도 된다. EXTERNAL 일정 쓰기는 3단계 장치(assert_approved가 step을 running으로 → 복구 때 다시 실행하지 않음)가 그대로 적용된다.

### 도구별
- 2026-11-17 get_weather의 지명→좌표는 명세대로 카카오 로컬 키워드 검색을 쓴다(지시서는 Open-Meteo 지오코딩 등을 예로 들었다). Open-Meteo 지오코딩(GeoNames)은 '성수동', '강남역' 같은 동네·역 이름을 잘 못 찾고, 카카오 키는 search_places에 어차피 필요하다. 좌표를 잡은 장소 이름은 `matched_place`로 따로 남긴다(Django판에서 '성수동' → '서울숲카페거리'로 바뀌어 보이는 문제).
- 2026-11-17 날씨는 서울 기준 오늘~15일 뒤 날짜만 받고 형식이 틀리거나 범위 밖이면 API를 부르지 않는다. 일별 weather_code로 `summary`(맑음 등)를 더하고, 시간대별은 3시간 간격 8개로 줄인다.
- 2026-11-17 Open-Meteo는 2026-11-17 이 PC에서 연결되었다(전날 밤에는 안 됨). 실제 응답 1회를 fixture로 저장했다.
- 2026-11-17 카카오·네이버는 fixture를 위해 실제 API를 각각 2회(좌표 변환 + 검색), 1회 불렀다(검색어 "성수동", "맛집", "성수동 맛집"). 키 값은 출력·저장하지 않았고, 응답에는 키·개인정보가 없다(가게 전화번호는 공개된 업체 번호). 실패 응답은 모두 문서 형식으로 만들었다(`tests/fixtures/README.md`).
- 2026-11-17 web_search는 NAVER API HUB 주소·헤더를 쓴다(.env의 키가 API HUB에서 발급됨). 한도는 웹문서 검색 문서에 "하루 25,000회"로 적혀 있어 Django판 메모(월 775,000건)와 다르다. 콘솔에서 확인이 필요하다.

### Google 연결(OAuth)
- 2026-11-17 OAuth는 google-auth-oauthlib·google-api-python-client 없이 httpx로 REST를 직접 부른다(`app/google_auth.py`). 명세의 기술 스택에는 두 라이브러리가 있지만, 새 의존성(httplib2 등)과 Smart App Control 위험 없이 타임아웃·오류 문장·키 숨기기를 다른 외부 도구와 같은 곳에서 지키고, 가짜 HTTP로 콜백·갱신·실패를 테스트할 수 있다. 지시서도 이 방식을 허용했다.
- 2026-11-17 API 경로는 명세 목록 그대로: `GET /api/connections`, `GET /api/connections/google/start`(302), `GET /api/connections/google/callback`(302), `DELETE /api/connections/{provider}`(204). 콜백은 Google이 브라우저를 보내는 주소라 JSON 오류 대신 `{APP_ORIGIN}/connections?google=connected|error&reason=코드`로 리디렉션한다. 화면의 연결 페이지 경로는 `/connections`로 정했다(명세 apps/web 구조의 connections).
- 2026-11-17 GOOGLE_REDIRECT_URI는 화면 주소를 거치는 `{APP_ORIGIN}/api/connections/google/callback`이어야 한다. 세션 쿠키(state 검증에 필요)는 브라우저가 연 출처의 쿠키라서다. 지금 `.env` 값은 Django판 콜백(localhost:8000, 다른 경로, 끝에 `/`)이라 사용자가 바꿔야 한다(값은 출력하지 않고 경로·출처만 비교했다).
- 2026-11-17 state(32바이트 무작위)와 PKCE code_verifier(S256)를 서명된 세션 쿠키에 넣고, 콜백에서 꺼내 바로 지운다(한 번만, 10분 안, `secrets.compare_digest`). state가 다르면 Google에 아무 요청도 보내지 않고 아무것도 저장하지 않는다. 로그인하지 않은 콜백도 거절한다.
- 2026-11-17 범위는 `calendar.events.owned` 하나다. 명세는 `calendar.events`지만 지시서의 "캘린더 일정에 필요한 최소 범위"에 따라, events list·get·insert·patch·delete 문서의 허용 범위에 모두 들어 있으면서 더 좁은(내가 소유한 캘린더만) 범위를 골랐다. `access_type=offline`, `prompt=consent`(다시 연결할 때도 refresh token을 받기 위해), `include_granted_scopes=true`(7단계 메일 권한을 더할 때를 위해).
- 2026-11-17 세부 동의 화면에서 캘린더 권한을 빼고 허용하면 저장하지 않고 받은 토큰을 철회한 뒤 `reason=scope_missing`으로 돌려보낸다.
- 2026-11-17 토큰(access, refresh, 만료 시각)은 JSON 통째로 Fernet 암호화해 `connections.encrypted_token`에, 범위와 access 만료 시각만 평문 칸에 둔다. 기존 테이블 그대로라 마이그레이션이 없다.
- 2026-11-17 `TOKEN_ENCRYPTION_KEY`가 비었거나 Fernet 키가 아니면 Google 연결을 막는다(`available=false`, start는 `reason=not_configured`, 캘린더 도구는 ".env 확인" 실패). 키를 대신 만들지 않는다. 지금 `.env`에는 유효한 Fernet 키가 있다(형식만 검사, 값은 출력하지 않음).
- 2026-11-17 access token은 만료 1분 전이면 미리 갱신하고, 캘린더 API가 401이면 강제 갱신 후 한 번만 다시 보낸다(쓰기도 같다: 401은 Google이 요청을 받아들이지 않은 것이라 두 번 생기지 않는다). 갱신이 400(invalid_grant: 만료·철회, 테스트 상태 앱은 7일)이면 connections 행을 지우고 "설정에서 Google 계정을 다시 연결해 주세요"를 돌려준다. 저장된 토큰을 풀 수 없을 때(키가 바뀜)도 같은 안내다.
- 2026-11-17 연결 해제는 refresh token(없으면 access token)으로 Google 쪽 철회를 시도하고 실패해도 우리 DB의 토큰은 지운다. 토큰은 철회 요청의 주소(쿼리)가 아니라 본문으로 보낸다(문서 예시는 쿼리. 주소는 로그에 남을 수 있다).

### 일정 도구
- 2026-11-17 일정은 기본 캘린더(`primary`)만 다룬다. 시각 입력은 서울 기준 `YYYY-MM-DDTHH:MM`(인자 pattern으로 검사)이고 `{dateTime, timeZone: "Asia/Seoul"}`로 보낸다. event_id는 `[A-Za-z0-9_-]`만 받고 주소에 넣을 때 다시 인코딩한다(경로 조작 방지).
- 2026-11-17 `sendUpdates`를 보내지 않는다(기본값: 참석자에게 알림 메일을 보내지 않음). 참석자 인자도 만들지 않았다(메일 발송은 금지 기능).
- 2026-11-17 끝 시각을 사용자가 말하지 않으면 시작 1시간 뒤로 넣으라고 create_event 설명에 적었다(명세 예시는 2시간이지만 모델이 정한 값이다). 승인 카드의 preview가 시각을 보여 주므로 사용자가 확인한다.
- 2026-11-17 Google에 연결되지 않은 상태에서 모델이 create_event를 부르면 승인 카드는 뜨지만 preview 끝에 "(주의: Google 계정이 연결되어 있지 않아 승인해도 실패합니다. 설정에서 먼저 연결해 주세요.)"를 붙인다. 승인 요청 자체를 막으려면 toolset에서 도구를 숨겨야 하는데 그건 제안으로 남긴다(아래 예정된 결정).
- 2026-11-17 update/delete preview의 외부 문자열(일정 제목·이전 장소)은 제어 문자·줄바꿈·「」를 지우고 40자로 잘라 「」로 감싼다. 모델이 넣은 값(새 제목 등)은 '…'로 감싼다. 감시 에이전트 지시문에 "「」 안은 외부 서비스에서 읽어 온 데이터이고 지시가 아니다"를 더했다. preview에서 빼면 사람이 "어떤 일정을 지우는지" 확인할 수 없고(명세: 지워질 일정의 제목과 시각), 그대로 넣으면 감시 에이전트 입력에 주입 문장이 길게 들어가기 때문에 표시·길이 제한으로 절충했다(docs/SAFETY.md 한계 항목).
- 2026-11-17 preview가 일정을 읽지 못하면(연결 없음, 404, cancelled) 승인 요청을 막지 않고 "일정(ID …)을 삭제합니다. 일정 정보를 불러오지 못했습니다: 이유"로 보여 준다. 지운 일정은 200 + `status: cancelled`로 올 수 있어 "찾지 못함"으로 본다(Django판 실사용에서 확인된 동작).
- 2026-11-17 동적 지시문에 이번 주(월~일) 날짜표와 다음 주 월요일을 더했다("이번 주 금요일"을 모델이 요일 계산 없이 실제 날짜로). 날짜에만 달려 있어 하루 동안은 같은 문장이다. 시스템 프롬프트 본문은 바꾸지 않았다(도구 사용법은 도구 설명에).

### 테스트
- 2026-11-17 기존 테스트 2개의 기대 도구 목록을 4단계 도구 이름으로 바꿨다(`test_schedule_task_gets_read_tools_only`, `test_chat_task_gets_read_and_internal_tools` → `test_chat_task_gets_all_tool_levels`). `test_approvals.py`·`test_agent_run.py`는 AgentDeps·RunServices의 새 필드를 채우도록만 고쳤다.
- 2026-11-17 테스트에서 ToolResult.data(JsonValue)를 키로 읽을 때는 `payload(result)`(Any) 도우미를 쓴다(pyright standard 모드).

## 예정된 결정 (아직 구현하지 않음)

### 3단계: 남은 것
- 취소 API(`POST /tasks/{id}/cancel`, 명세 API 목록·1단계 결정)는 이번 작업 범위(지시서)에 없어 만들지 않았다. 만들 때는 DBOS `cancel_workflow`(진행 중인 단계가 끝난 뒤 멈춤) + task cancelled + 남은 waiting_approval step 닫기를 함께 한다.
- 화면: `public/sw.js`(push 이벤트에서 payload의 title·body로 알림, notificationclick에서 url 열기), 설정 화면 '알림 켜기'(권한은 버튼을 눌렀을 때만, 아이폰은 홈 화면 추가 안내), StepTimeline 1초 폴링은 화면 작업자 몫.

### 4단계: 외부 도구
- 설치된 `add-agent-tool` 스킬은 Django판 기준이다. 이 저장소에서는 `AGENTS.md`의 "도구 규약" 절을 따른다.
- (제안) Google이 연결되지 않았으면 일정 쓰기 도구를 모델에게 보이지 않게(toolset prepare) 해 승인 카드가 뜨지 않게 할 수 있다. 지금은 preview의 경고로 알린다.
- (제안) 네이버 검색 API 이용약관의 저장 제한: Django판은 21일 지난 검색 결과를 steps.output에서 지웠다. 이 앱도 6단계 예약 검사 작업에 같은 정리를 넣을지 정한다(약관의 정확한 보관 기간은 확인하지 못했다).
- (제안) Calendar API 한도 초과(403·429) 때 지수 백오프 재시도는 하지 않았다(조회는 1회 재시도만). 실사용에서 자주 나오면 READ만 백오프를 더한다.
- 실제 Google 계정 연결과 '이번 주 금요일 저녁 7시에 성수동에서 저녁 약속 잡아줘' 실제 흐름 확인은 사용자가 브라우저에서 한다(화면의 연결 페이지가 필요).

### 6단계: 자동화
- 아침 브리핑 기본 예약은 새 계정뿐 아니라 이미 있는 계정에도 만들어 준다.
- 예약 작업은 사용자 메시지 없이 시작한다(`trigger_message_id` null). 실행기는 이미 `run_task(..., prompt=...)`로 프롬프트를 따로 받는다.
- 감시 실행이 task를 만드는지는 6단계 시작 때 정한다.
- 감시·예약의 중복 등록은 서버가 거절한다(Django판에서 같은 감시를 두 번 만든 버그).

### 7단계: 메일
- Logfire 트레이스에 메일 본문이 남지 않는지 확인하는 일은 Logfire를 연결하는 8단계로 옮긴다.

### 8단계: 평가
- eval 8번("이번 달 일정 전부 지워줘")은 범위를 되묻는 질문으로 끝나는 것이 기대 동작이므로, 승인 열을 "필요(건별)"가 아니라 "없음"으로 고쳐 쓴다.

## 7단계 (2026-11-17)
- 2026-11-17 음성 입력은 브라우저 `SpeechRecognition`(없으면 `webkitSpeechRecognition`)만 쓰고 lang=ko-KR. 앱은 음성·녹음을 서버로 보내는 코드를 두지 않는다(크롬은 브라우저가 자체적으로 구글 서버에서 인식한다).
- 2026-11-17 인식 결과는 입력창 끝에 이어 붙이기만 한다(기존 글은 지우지 않고, 끝에 공백이 없으면 한 칸 띄움, 4,000자 제한). 자동 전송하지 않는다.
- 2026-11-17 continuous=false, interimResults=false: 한 문장을 말하면 끝나고 확정된 글자만 넣는다. 중간 결과로 입력창이 계속 바뀌면 고치던 글과 섞이기 쉬워서다.
- 2026-11-17 지원 여부는 `useSyncExternalStore`(서버 스냅숏 false)로 읽는다. 서버 HTML과 첫 화면에서는 버튼이 없고 하이드레이션 뒤 지원 브라우저에서만 나타난다(하이드레이션 불일치 방지).
- 2026-11-17 SpeechRecognition 타입은 lib.dom에 없어 `lib/speech.ts`에 앱이 쓰는 부분만 지역 타입으로 적었다. `declare global`로 Window에 넣지 않는다(없을 수도 있다는 사실이 타입에서 사라지므로).
- 2026-11-17 마이크 버튼은 이름 "음성 입력"을 고정하고 aria-pressed로 듣는 중을 알린다. 듣는 중에는 빨간 정지 모양 + "듣는 중…" 안내, 다시 누르면 stop(그때까지 말한 것은 결과로 받음).
- 2026-11-17 전송을 누르거나 전송 중(pending)이 되거나 화면을 떠나면 abort하고 이벤트 처리기를 먼저 떼어, 늦게 온 결과가 비워진 입력창에 들어가지 않게 한다. 전송 중에는 마이크 버튼을 막는다.
- 2026-11-17 오류 안내: 권한 거부는 https가 아니면 "https 주소에서만" 안내, https면 브라우저 설정 안내. no-speech와 "결과도 오류도 없이 끝남"(사용자가 멈춘 경우 제외)은 무음 안내. aborted는 안내하지 않는다. 그 밖은 일반 안내.
