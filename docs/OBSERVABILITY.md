# 관찰(Logfire) — 8단계

코드: `apps/api/app/observability.py`(설정), `apps/api/app/main.py`(`create_app`에서 부름).
테스트: `apps/api/tests/test_observability.py`(실제 전송 없이 메모리 exporter로 스팬을 받아 검사).
설치된 버전: logfire 5.6.0, pydantic-ai-slim 2.68.0(`[logfire]` 추가), opentelemetry-instrumentation-fastapi 0.66b2.
확인한 문서: https://logfire.pydantic.dev/docs/ , https://pydantic.dev/docs/ai/logfire/ (설치된 패키지의 docstring·소스로 대조).

## 한 줄 요약

- **`TELEMETRY_ENABLED=true`와 `LOGFIRE_TOKEN`이 함께 있을 때만 보낸다**(8단계 보강). 둘 중 하나라도 없으면 아무것도
  밖으로 보내지 않는다(`send_to_logfire=False`, 내보내기 없음). 기본값은 false다.
- 환경변수로 우회되지 않는다(설치된 logfire 5.6.0 `logfire/_internal/config.py`로 확인하고 테스트로 고정):
  - `LOGFIRE_TOKEN`·`LOGFIRE_SEND_TO_LOGFIRE` 환경변수: `send_to_logfire`를 늘 명시하므로 쓰이지 않는다.
  - `OTEL_EXPORTER_OTLP_ENDPOINT`·`_TRACES_`·`_METRICS_`·`_LOGS_ENDPOINT`: logfire는 `send_to_logfire`와 **상관없이** 이 주소로
    가는 OTLP 내보내기(트레이스·지표·로그)를 더한다(토큰이 비어 있어도. 보강 전 코드에서 테스트로 재현). 그래서
    `logfire.configure()`가 도는 동안만 이 네 환경변수를 숨기고 끝나면 되돌린다(`otlp_environment_hidden`). 켜져 있을 때도
    이 앱이 보내는 곳은 Logfire 하나다.
  - `LOGFIRE_FLEET`·`OTEL_OPAMP_*`(원격 관리): `fleet=False`를 명시한다.
  - DBOS: 설치된 dbos 3.5.0 패키지 소스에는 otlp·opentelemetry를 쓰는 코드가 없다(문자열 검색으로 확인).
- 보내도 트레이스에는 **내용이 없고 모양만** 있다: 어떤 요청이 왔고, 에이전트가 모델을 몇 번 불렀고, 어떤 도구를
  얼마나 걸려 실행했고, 토큰을 몇 개 썼는지. 프롬프트·지시문·모델 답변·도구 인자·도구 결과(메일 본문, 검색 결과, 일정 제목),
  예외 메시지, URL 쿼리, 헤더·쿠키는 들어가지 않는다.

## 무엇을 계측하나

| 대상 | 방법 | 남는 것 | 남지 않는 것 |
| --- | --- | --- | --- |
| HTTP 요청 | `logfire.instrument_fastapi(app, capture_headers=False, request_attributes_mapper=→None)` | 메서드, 경로 틀(`/api/conversations/{conversation_id}/messages`), 실제 경로, 상태 코드, 걸린 시간 | 헤더·쿠키, 요청 본문·엔드포인트 인자 값(메시지 내용, 로그인 비밀번호), 쿼리 문자열(서버 훅이 지움) |
| Google OAuth 콜백 | `excluded_urls`로 제외 | 아무것도 | 콜백 전체(쿼리에 OAuth code·state가 온다) |
| 에이전트(메인·감시·감시 판정) | `logfire.instrument_pydantic_ai(include_content=False, include_binary_content=False)` | 에이전트 이름, 모델 이름, 모델 요청 횟수, 토큰 수, 도구 이름, tool_call_id, 도구 정의(이름·설명·인자 스키마) | 시스템 지시문, 사용자 메시지, 모델 답변, 도구 인자, 도구 결과, 예외 메시지 |
| 예외 | Logfire `exception_callback`(`hide_exception_details`) | 예외 종류, 코드 위치(파일·줄·함수) | 예외 메시지(SQL 매개변수·외부 응답·사용자 글이 들어갈 수 있다), 오류 상태 설명 |
| 그 밖에 새어 나온 값 | Logfire 기본 가리기 + 이 앱의 패턴 | — | 이름이 password·secret·api key·session·cookie·token류인 값, 그리고 값 모양이 Anthropic 키(`sk-ant-`), Google 토큰(`ya29.`, `1//0`), Fernet 암호문(`gAAAAA`), 카카오 인증(`KakaoAK`), `Bearer `, vapid·oauth·code_verifier 등인 값 |

계측하지 않는 것: 외부 API 호출(httpx), DB 쿼리, Python `logging` 로그, DBOS 자체 트레이스(설치된 dbos 3.5.0에는 OTLP·OpenTelemetry 코드가 없다. 8단계 보강에서 소스로 다시 확인).
외부 API 주소에는 검색어가, DB 쿼리에는 매개변수가 들어가므로 일부러 켜지 않았다. 필요해지면 가리기를 확인한 뒤 켠다.
**고칠 때 주의(2026-11-19)**: KOBIS 오픈API는 키를 쿼리 문자열(`key=`)로만 받는다(`app/tools/kobis.py`). httpx 계측이나 주소를 남기는
로그를 켜면 KOBIS 키가 그대로 남으니, 켜기 전에 쿼리 문자열을 지우는 가리기를 먼저 넣는다(TMAP·TMDB 키는 헤더라 해당 없음).

## 작업 하나가 트레이스에서 보이는 모양

"오늘 온 메일 요약해줘"를 보내 모델이 `read_mail`을 한 번 부르고 답한 경우(테스트에서 받은 스팬을 부모-자식 순으로 정리.
테스트에서는 모델 이름이 가짜 모델 이름 `scripted`로 찍히고, 실제로는 `claude-haiku-5-5` 같은 모델 이름이다):

```text
POST /api/conversations/{conversation_id}/messages        http.status_code=202, 걸린 시간
└─ invoke_agent myagent                                    gen_ai.aggregated_usage.input_tokens / output_tokens
   │                                                       pydantic_ai.all_messages = [{"role":"user","parts":[{"type":"text"}]},
   │                                                         {"role":"assistant","parts":[{"type":"tool_call","name":"read_mail"}]}, ...]
   │                                                         ← 역할·종류·도구 이름만 있고 글(content)은 없다
   ├─ chat claude-haiku-5-5                                 모델 요청 1: gen_ai.usage.input_tokens, 응답 모델, 걸린 시간
   ├─ execute_tool read_mail                                도구 이름, tool_call_id, 걸린 시간 (인자·결과 없음)
   └─ chat claude-haiku-5-5                                 모델 요청 2(최종 답변)
GET /api/tasks/{task_id}                                   화면의 1초 폴링(요청마다 짧은 트레이스 하나)
```

- 승인이 필요한 작업은 `invoke_agent myagent`가 `execute_tool` 없이 끝나고(승인 대기), 이어서 감시 에이전트 `invoke_agent myagent-guard`가
  보인다. 사용자가 승인하면 `POST /api/approvals/{approval_id}/approve` 아래에 재개 run(`invoke_agent myagent`)과 `execute_tool create_event`가 보인다.
- 조건 감시는 `invoke_agent myagent-watch`(감시 판정)가 HTTP 요청 없이 따로 보인다(1분 검사가 시작한 워크플로).
- **확인하지 못한 것**: 테스트는 워크플로가 끝날 때까지 기다리는 시작기를 써서 에이전트 스팬이 POST 요청 아래에 붙는다. 실제 서버는
  202로 바로 응답하고 DBOS가 워크플로를 따로 돌린다. DBOS가 같은 이벤트 루프에서 작업을 시작하면 컨텍스트가 이어져 같은 트레이스에
  붙고, 이어지지 않으면 `invoke_agent myagent`가 별도 트레이스로 보인다. 서버가 죽었다 살아나 복구된 워크플로는 항상 별도 트레이스다.
  토큰을 넣은 뒤 아래 절차로 확인한다.

## 토큰을 넣은 뒤 사용자가 볼 절차

1. https://logfire.pydantic.dev 에서 프로젝트를 만들고 Settings → Write tokens에서 쓰기 토큰을 만든다.
2. 루트 `.env`의 `LOGFIRE_TOKEN='...'`에 넣고 `TELEMETRY_ENABLED=true`로 바꾼 뒤 API 서버를 다시 켠다(`pnpm api`).
   토큰 값은 채팅·문서·커밋에 붙이지 않는다.
3. 화면에서 "오늘 일정 알려줘"처럼 도구를 쓰는 메시지를 하나 보낸다(Google 연결이 없으면 "내 할 일 보여줘").
4. Logfire Live 화면에서 service `myagent-api`를 고르고 다음을 본다.
   - `POST /api/conversations/{conversation_id}/messages` → `invoke_agent myagent` → `chat …`/`execute_tool …`가 보이는가
     (에이전트 스팬이 별도 트레이스로 떨어져 있으면 위 "확인하지 못한 것"이 그 경우다. 동작에는 문제가 없다).
   - 스팬을 열어 속성에 **보낸 문장, 답변, 도구 인자·결과, 메일 내용이 없는지** 확인한다. `pydantic_ai.all_messages`에는
     `{"type":"text"}`처럼 글 없는 칸만 있어야 한다.
   - 메일 권한이 있으면 "오늘 온 메일 요약해줘"도 한 번 보내 `execute_tool read_mail`에 본문이 없는지 본다(명세 7단계 5번).
   - 설정에서 Google을 다시 연결해 보고 콜백(`/api/connections/google/callback`) 트레이스가 **없는지** 본다.
5. 무엇이든 내용이 보이면 `TELEMETRY_ENABLED=false`로 바꾸고 서버를 다시 켠 뒤 알려 준다(바로 전송이 멈춘다).

## 테스트로 확인한 것 (실제 전송 없음)

| 테스트 | 확인 |
| --- | --- |
| `test_empty_token_never_sends` | 토큰이 비면 OTLP 전송 함수(`requests.Session.send`)가 한 번도 불리지 않는다 |
| `test_token_and_flag_turn_sending_on` | TELEMETRY_ENABLED=true와 토큰이 함께 있을 때만 `send_to_logfire=True`, `fleet=False`(설정 값만 확인) |
| `test_telemetry_flag_is_off_by_default_and_read_from_environment` | 기본 false, 환경변수 `TELEMETRY_ENABLED=true`로만 켜진다 |
| `test_token_without_telemetry_flag_is_not_sending` | 토큰만 있으면 보내지 않고 토큰도 넘기지 않는다 |
| `test_otlp_environment_cannot_bypass_the_flag` (8가지) | 토큰 없음·있음 × `OTEL_EXPORTER_OTLP_*_ENDPOINT` 네 가지, 환경변수 `LOGFIRE_TOKEN`·`LOGFIRE_SEND_TO_LOGFIRE=true`까지 있어도 OTLP 내보내기를 만들지 않고 전송 시도가 없다. 환경변수는 그대로 남는다. 보강 전 코드에서는 토큰이 비어 있어도 4가지 모두 내보내기가 만들어지고 전송을 시도했다 |
| `test_mail_task_trace_has_no_content` | 메일을 읽는 작업의 트레이스에 메일 본문 낱말, 사용자 메시지, 답변, 도구 인자, 테스트 키, 세션 쿠키 값이 없다. 스팬 모양(POST → invoke_agent → execute_tool read_mail)과 토큰 수는 있다 |
| `test_query_strings_are_not_traced` | 쿼리 문자열이 없고, OAuth 콜백은 트레이스가 없다 |
| `test_exception_messages_are_hidden`, `test_unhandled_route_exception_message_is_hidden` | 예외 종류는 남고 메시지는 없다 |
| `test_key_shaped_values_are_scrubbed` | 이름으로는 걸리지 않는 칸의 키·토큰 모양 값이 가려진다 |

각 장치(include_content=False, 쿼리 훅, 예외 콜백, 추가 패턴)를 하나씩 빼면 해당 테스트가 실패하는 것을 확인했다(8단계).
8단계 보강(2026-11-17)에서 내용 수집 비활성화(`include_content=False`, `include_binary_content=False`)는 바꾸지 않았고,
`test_mail_task_trace_has_no_content`를 다시 돌려 메일 본문·사용자 메시지·답변·도구 인자가 트레이스에 없음을 확인했다.
