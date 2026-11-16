# 안전 설계 (초안)

프롬프트는 모델이 따를 수도 안 따를 수도 있다. 그래서 이 앱의 안전 장치는 가능한 한 코드와 DB 제약으로 둔다.
이 문서는 단계마다 "어떤 원칙을 어디서 막는지"를 쌓는다.

## 위험 등급

| 등급 | 뜻 | 처리 |
| --- | --- | --- |
| READ | 조회만 한다 | 자동 실행 |
| INTERNAL | 앱 안에서만 바뀌고 되돌릴 수 있다 | 자동 실행 후 기록 |
| EXTERNAL | 앱 밖에 흔적이 남는다 | 매번 사용자 승인 (이중 방어) |
| FORBIDDEN | 결제, 전화, 메시지 발송, 비밀번호 입력 | 도구 자체를 만들지 않는다 |

## 절대 원칙과 막는 위치 (계획)

| 원칙 | 막는 위치 | 단계 |
| --- | --- | --- |
| EXTERNAL은 승인 없이 실행 불가 | requires_approval + 도구 첫 줄 assert_approved(DB 승인 기록) | 2 |
| 자동 작업은 READ만 | origin별 툴셋 선택, schedule·watch task의 승인 생성 금지 | 1, 6 |
| 기억은 사용자가 말한 것만 | remember 서버 검사(origin=chat, 외부 콘텐츠 도구 미사용) | 5 |
| 도구 결과는 데이터 | 외부 콘텐츠 감싸기, 감시 에이전트에 도구 결과를 넣지 않음 | 1, 2 |
| 키는 .env와 암호화 컬럼에만 | SecretStr, Fernet, 로그 가리기 | 0, 4, 8 |

## 0단계에서 적용한 것

- 회원가입 화면·API가 없다. 계정은 `python -m app.cli create-user`로만 만든다.
- 비밀번호는 bcrypt로 해시한다. 없는 이메일도 더미 해시를 검사해 응답 시간으로 계정 존재를 알 수 없게 하고, 401 문구는 무엇이 틀렸는지 말하지 않는다.
- 세션은 `SESSION_SECRET`으로 서명된 쿠키다. HttpOnly, SameSite=Lax. `APP_ORIGIN`이 https일 때만 Secure(로컬 HTTP로 폰 로그인을 확인하기 위해, docs/DECISIONS.md).
- 로그인할 때 세션을 비우고 새로 쓴다(세션 고정 방지).
- 상태를 바꾸는 요청은 Origin이 `APP_ORIGIN`과 정확히 같아야 한다. 다르거나 없으면 403. 앱 전체에 미들웨어로 걸려 있어 라우터마다 빠뜨릴 수 없다. CORS는 열지 않는다.
- 모든 조회에 `user_id` 조건을 건다. 남의 대화는 404(존재 여부도 드러내지 않음).
- API 서버와 DB는 127.0.0.1에만 열린다. 같은 와이파이의 다른 기기는 Next.js(3000)를 통해서만 API에 닿는다.
- 설정의 키·토큰은 `SecretStr`이라 실수로 로그에 찍혀도 `**********`로 보인다.
- `.gitignore`는 `.env*`를 막고 `.env.example`만 예외로 둔다(`.env.django` 같은 복사본도 막힌다).
- 테스트는 실제 `.env`를 읽지 않는 설정(`SettingsWithoutEnvFile`)으로 앱을 만든다. 테스트 DB 이름이 `_test`로 끝나지 않으면 지우지 않는다.
- `watches.interval_minutes >= 30`은 DB CHECK 제약으로도 막는다.

## 1단계에서 적용한 것

### 도구 예외 규칙
- **모든 도구는 `ToolResult`를 돌려주고 예외를 밖으로 내지 않는다.** 실패는 `ok=false`와 한국어 이유(`error`)로 돌려준다.
- **예상 못 한 예외도 한 곳에서 잡는다.** `app/agent/tracking.py`의 `StepTracking.on_tool_execute_error`가 도구 안에서 난
  예외를 `ToolResult(ok=False, error="도구를 실행하는 중 예상하지 못한 오류가 발생했습니다.")`로 바꾼다.
  내부 오류 내용(예외 메시지, DB 정보)은 로그에만 남기고 모델과 화면에 보내지 않는다. step은 failed로 기록된다.
- **유일한 예외는 `PermissionError`다.** 2단계의 `assert_approved`가 승인 기록 없는 EXTERNAL 실행을 막을 때 던진다.
  이것을 ok=false로 삼키면 "승인 없이 실행하려 했다"는 사실이 숨겨지므로 그대로 올려 보낸다(step은 failed로 남긴다).
  Pydantic AI가 흐름 제어에 쓰는 예외(ModelRetry, ApprovalRequired, CallDeferred 등)도 그대로 통과시킨다.
- run 전체에서 난 예외(모델 응답 이상, 설정 오류 등)는 실행기(`runner.py`)가 잡아 task를 failed로 끝낸다. 서버는 죽지 않는다(테스트 8).

### 그 밖의 장치
- 자동 작업은 READ만: origin이 schedule·watch인 task에는 `read_tools`만 넘긴다. 넘기지 않은 도구는 모델에게 보이지도 않고 실행되지도 않는다(테스트 5).
- EXTERNAL toolset은 비어 있지만 이미 `requires_approval=True`로 만들어 두었다. 등급이 없는 도구는 기록상 EXTERNAL로 본다.
- 모든 도구의 조회·수정에 `user_id` 조건을 건다. 작업 조회 API도 남의 작업은 404.
- 상한 세 가지(요청 수, 토큰, 하루 비용)를 넘으면 budget_exceeded로 멈추고, 그때까지의 step은 남는다(테스트 6).
- 메시지 전송은 사용자당 분당 20회(429).
- 테스트는 실제 모델 요청을 전역으로 막는다(`ALLOW_MODEL_REQUESTS = False`).
- API 키는 `os.environ`에 넣지 않고 설정 객체에서 모델 공급자로 바로 넘긴다(`model_choice.py`).

## 알려진 한계

- 서명 쿠키 세션은 개별 세션을 서버에서 끊을 수 없다. 쿠키를 도난당하면 14일 동안 유효하다. 대응은 `SESSION_SECRET` 교체(전체 로그아웃).
- 로컬 HTTP에서는 쿠키에 Secure가 없다. 배포(9단계)에서는 https `APP_ORIGIN`으로 Secure가 자동으로 켜진다.
- 로그인 시도 횟수 제한은 아직 없다.
- 전송 제한은 프로세스 메모리에 있다. 서버를 재시작하면 초기화되고, 프로세스를 늘리면 프로세스마다 따로 센다.
- 가격표(`MODEL_PRICES`)에 없는 모델은 비용 0으로 기록되어 하루 비용 상한에 잡히지 않는다(경고 로그는 남긴다). 모델을 바꾸면 가격표도 함께 고친다.
- 1단계에서는 메시지 전송 API가 run이 끝날 때까지 응답을 기다린다. 오래 걸리는 작업은 브라우저 요청이 길어진다(3단계에서 바뀐다).
