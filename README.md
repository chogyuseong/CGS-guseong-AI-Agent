# 마이에이전트

채팅으로 시킨 일을 공식 API 도구로 대신 처리하는 개인 AI 비서 웹앱(모바일 우선 PWA).
앱 밖에 흔적이 남는 행동은 매번 사람의 승인을 거치도록 **서버 코드로** 막았다.

> 이 README는 3단계 기준 초안이다. 구조 그림, 스크린샷, 배포 안내는 8단계에서 채운다.
> 자세한 내용: [구조](docs/ARCHITECTURE.md) · [설계 결정](docs/DECISIONS.md) · [안전 설계](docs/SAFETY.md)

## 실행 방법 (개발)

처음 한 번:

```powershell
py -3.12 -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy .env.example .env                                  # SECRET_KEY, ANTHROPIC_API_KEY를 채운다
.venv\Scripts\python manage.py migrate
.venv\Scripts\python manage.py createsuperuser
.venv\Scripts\python manage.py generate_vapid_keys --write-env   # 웹 푸시 키 (VAPID_SUBJECT는 직접)
```

매번, **터미널 두 개**에서:

```powershell
# 터미널 1: 웹 서버
.venv\Scripts\python manage.py runserver

# 터미널 2: 백그라운드 워커 (메시지 처리, 승인 후 이어 가기, 만료 정리)
.venv\Scripts\python manage.py run_worker
```

- 워커가 꺼져 있으면 메시지를 보내도 "차례를 기다리는 중"에서 멈춰 있다.
- **SQLite(개발 기본값)에서는 워커를 하나만 띄운다.** SQLite는 다른 워커가 잡은 행을 건너뛰는 기능(`skip_locked`)이 없다.
  같은 Task가 두 번 실행되지는 않지만(조건부 UPDATE로 막음), 여러 워커가 서로 기다리느라 느려진다.
  워커를 여러 개 띄우려면 PostgreSQL을 쓴다(`DATABASE_URL`).
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
