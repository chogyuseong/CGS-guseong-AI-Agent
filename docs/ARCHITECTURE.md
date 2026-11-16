# 구조 (초안, 0단계)

마이에이전트는 채팅이나 음성으로 시킨 일을 공식 API 도구로 대신 처리하는 개인 AI 비서 웹앱이다.
기능 수보다 **통제 구조**를 먼저 설계했다. 자세한 안전 설계는 [SAFETY.md](SAFETY.md), 결정 기록은 [DECISIONS.md](DECISIONS.md)를 본다.

## 요청 한 건의 흐름

```
사용자 메시지
   │
   ▼
Task(queued) ──► run_task ──► LLM이 도구 선택 ──► Step 기록
                    ▲                                │
                    │            ┌───────────────────┼────────────────────┐
                    │            ▼                   ▼                    ▼
                    │      READ / INTERNAL       EXTERNAL            없는 도구·잘못된 입력
                    │       바로 실행          preview → 감시 에이전트      실행하지 않고
                    │            │              → Approval(pending)      오류를 돌려줌
                    │            │              → Task 멈춤(승인 대기)
                    │            │                   │ 사용자가 승인/거절
                    └────────────┴───────────────────┘
                                 │ 최종 답변이면
                                 ▼
                         Message 저장, Task done
```

- 한 Task는 최대 12 Step까지. 단계 수, 토큰, 하루 비용 상한을 넘으면 `budget_exceeded`로 멈춘다.
- 스스로 도는 작업(예약 작업, 조건 감시)은 READ 도구만 쓴다.

## 앱 구성

| 앱 | 맡은 일 | 모델 |
| --- | --- | --- |
| `config` | 설정, URL, 공통 베이스 모델(`TimeStampedModel`) | - |
| `accounts` | 로그인, 외부 서비스 연결, 설정 화면 | Connection |
| `chat` | 대화방과 채팅 화면 | Conversation, Message |
| `agent` | 에이전트 루프, 승인 게이트, 감시 에이전트, 비용 상한, 워커 | Task, Step, Approval, GuardCheck, UsageLog |
| `tools` | 도구 구현(도구 하나당 파일 하나)과 내부 도구 데이터 | Note, Todo |
| `memory` | 사용자가 직접 말한 기억 | Memory |
| `automation` | 예약 작업과 조건 감시 | ScheduledJob, Watch |
| `notifications` | 웹 푸시 구독과 발송 | PushSubscription |

## 데이터 모델 관계

```
User ─┬─< Conversation ─< Message
      │         └──────< Task ─< Step ─── Approval   (1:1)
      │                   │       └────── GuardCheck (1:1)
      │                   └──< UsageLog
      ├─< Connection   (user, provider 고유)
      ├─< Memory ──> Message(출처)
      ├─< Note, Todo
      ├─< ScheduledJob, Watch
      └─< PushSubscription
```

- 모든 모델에 `created_at`, `updated_at`이 있다.
- 상태값은 전부 `TextChoices`로 정의한다.
- Step, Approval, GuardCheck, UsageLog에는 user를 따로 두지 않고 `task.user`로 찾는다(같은 값을 두 번 저장하지 않기 위해).

## 실행 방법 (개발)

```powershell
py -3.12 -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy .env.example .env          # SECRET_KEY를 채운다
.venv\Scripts\python manage.py migrate
.venv\Scripts\python manage.py createsuperuser
.venv\Scripts\python manage.py runserver
```

- CSS를 바꿨다면: `bin\tailwindcss.exe -i static/src/input.css -o static/css/app.css --minify`
  (실행 파일은 [Tailwind 릴리스](https://github.com/tailwindlabs/tailwindcss/releases/tag/v4.1.11)에서 `tailwindcss-windows-x64.exe`를 `bin\tailwindcss.exe`로 저장)
- 테스트: `.venv\Scripts\python -m pytest`
- 린트와 포맷: `.venv\Scripts\ruff check .` / `.venv\Scripts\ruff format .`
- 3단계부터는 터미널 두 개에서 `runserver`와 `run_worker`를 함께 띄운다.
