# 마이에이전트

채팅으로 시킨 일을 공식 API 도구로 대신 처리하는 개인 AI 비서 웹앱(모바일 우선 PWA).
앱 밖에 흔적이 남는 행동은 매번 사람의 승인을 거치도록 **서버 코드로** 막았다.

> 이 README는 6단계(예약 작업·조건 감시·아침 브리핑)까지 반영한 초안이다. 구조 그림, 스크린샷, 배포 안내는 8단계에서 채운다.
> 자세한 내용: [구조](docs/ARCHITECTURE.md) · [설계 결정](docs/DECISIONS.md) · [안전 설계](docs/SAFETY.md)

## 지금 할 수 있는 것

| 기능 | 내용 | 승인 |
|---|---|---|
| 채팅 | 시킨 일을 도구로 처리하고, 거친 단계를 타임라인으로 보여 준다 | - |
| 메모, 할 일 | 앱 안에만 남는 기록 | 필요 없음 |
| 날씨, 장소 검색, 웹 검색 | Open-Meteo, 카카오 로컬, NAVER API HUB 검색 | 필요 없음(조회만) |
| Google 캘린더 | 일정 조회 / 등록, 수정, 삭제 | 등록, 수정, 삭제는 매번 승인 카드 |
| 기억 | "기억해 줘"로 저장, 기억 화면에서 원래 말 확인, 삭제 | 필요 없음 |
| 예약 작업 | "매주 일요일 밤 9시에 ~해줘". 정한 시각에 조회만 하고 결과를 대화방에 남긴다 | 행동 대신 [제안] 버튼 |
| 조건 감시 | "~하면 알려줘". 30분 이상 간격으로 확인, 맞으면 알림, 30일 뒤 만료 | 조회만 |
| 아침 브리핑 | 매일 07:30(설정에서 변경) 오늘 일정, 날씨, 남은 할 일 요약 | 행동 대신 [제안] 버튼 |
| 웹 푸시 | 승인 필요, 작업 완료·실패, 조건 충족 알림(고정 문구) | - |

안전장치(전부 서버 코드): 외부 행동 승인 게이트, 요청과 행동을 비교하는 감시 에이전트, 하루 비용 상한,
같은 도구 호출 반복 차단, 자동 작업에는 조회 도구만 전달. 자세한 내용은 [안전 설계](docs/SAFETY.md).

## 실행 방법 (개발)

처음 한 번:

```powershell
py -3.12 -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy .env.example .env      # SECRET_KEY, ANTHROPIC_API_KEY 필수. 카카오, NAVER, Google 키는 해당 도구를 쓸 때
.venv\Scripts\python manage.py migrate
.venv\Scripts\python manage.py createsuperuser
.venv\Scripts\python manage.py generate_vapid_keys --write-env   # 웹 푸시 키 (VAPID_SUBJECT는 직접)
```

매번, **터미널 두 개**에서:

```powershell
# 터미널 1: 웹 서버
.venv\Scripts\python manage.py runserver

# 터미널 2: 백그라운드 워커 (메시지 처리, 승인 후 이어 가기, 예약 작업·조건 감시 실행, 만료 정리)
.venv\Scripts\python manage.py run_worker
```

- 워커가 꺼져 있으면 메시지를 보내도 "차례를 기다리는 중"에서 멈춰 있다.
- **SQLite(개발 기본값)에서는 워커를 하나만 띄운다.** SQLite는 다른 워커가 잡은 행을 건너뛰는 기능(`skip_locked`)이 없다.
  같은 Task가 두 번 실행되지는 않지만(조건부 UPDATE로 막음), 여러 워커가 서로 기다리느라 느려진다.
  워커를 여러 개 띄우려면 PostgreSQL을 쓴다(`DATABASE_URL`).
- 예약 작업과 조건 감시는 워커가 60초마다 확인한다. 워커가 꺼져 있던 동안 지난 회차는 몰아서 실행하지 않는다.
- Google 캘린더 연결 때문에 `127.0.0.1`이 아니라 `http://localhost:8000`으로 접속한다.
- CSS를 고쳤다면 Tailwind standalone(v4.1.11)으로 다시 빌드한다:
  `bin	ailwindcss.exe -i static/src/input.css -o static/css/app.css --minify` (실행 파일은 깃에 없음)
- 워커는 Ctrl+C를 받으면 지금 처리 중인 작업을 끝낸 뒤 멈춘다.

## 테스트와 린트

```powershell
.venv\Scripts\python -m pytest     # 외부 API와 LLM은 전부 가짜. 네트워크와 요금을 쓰지 않는다
.venv\Scripts\ruff check .
.venv\Scripts\ruff format .
```

## 웹 푸시 알림

- 설정 화면의 **알림 켜기** 버튼을 눌렀을 때만 권한을 묻는다.
- 알림에는 고정 문구만 들어간다("승인이 필요한 작업이 있습니다" 등). 메시지 내용, 일정 제목은 넣지 않는다.
- 브라우저 정책상 **HTTPS 또는 localhost**에서만 동작한다. 폰에서 `http://PC-IP:8000`으로 접속하면 알림을 켤 수 없다(배포 후 확인).
- 아이폰은 Safari에서 **홈 화면에 추가**한 앱에서만 받을 수 있다(iOS 16.4 이상).
