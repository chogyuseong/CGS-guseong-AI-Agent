# 마이에이전트 (Next.js + FastAPI판) 시작 안내

이 폴더에서 Claude Code 새 세션을 열고, 아래 "첫 메시지"를 그대로 붙여 넣는다.
작성: 2026-11-16. 0단계가 끝나 AGENTS.md가 생기면 이 파일은 지워도 된다.

## 첫 메시지 (복사해서 붙여 넣기)

```text
SPEC.md와 START-HERE.md를 먼저 끝까지 읽어라.
SPEC.md의 "마스터 프롬프트" 블록이 이 프로젝트의 지시이고, "0단계: 뼈대" 블록이 지금 할 일의 상세다.
START-HERE.md의 "이 PC에서 알아 둘 것"과 "Django판에서 옮겨 올 것"은 마스터 프롬프트에 더해 지켜라.
0단계만 진행해라. 먼저 설치할 라이브러리의 현재 버전을 확인해 알려 주고, 계획을 보여 준 뒤 시작해라.
```

## 배경

- 같은 서비스의 Django판이 이미 있다: `C:\Users\rbtjd\Desktop\새 폴더 (3)\CGS-guseong-AI-Agent`
  (GitHub: chogyuseong/CGS-guseong-AI-Agent). 0~7단계와 사용량 화면까지 동작하고 테스트 388개가 통과한다.
- 2026-11-16에 사용자가 명세를 Next.js + FastAPI + Pydantic AI + DBOS판으로 바꾸고, 새 폴더에서 0단계부터 다시 만들기로 정했다.
- Django판은 수정하지 않는다. 읽기 전용 참고 자료다.
- 앱 이름은 "마이에이전트"를 그대로 쓴다. 발급해 둔 API 키도 그대로 쓴다.

## 이 PC에서 알아 둘 것

- **도구 버전(2026-11-16 확인)**: Python 3.12와 3.13(`py -3.12`), Node v26.13.0, Docker 29.7.2, Docker Compose v5.4.0,
  git 2.55, uv 0.13.8, pnpm 10.34.9.
- **Smart App Control이 켜져 있다.** 서명되지 않은 실행 파일은 "An Application Control policy has blocked this file"로 막힌다.
  - pnpm 12(네이티브 exe)가 이렇게 막혀서, Node로 도는 pnpm 10을 설치했다.
  - Biome, Playwright 브라우저, Next.js의 네이티브 모듈처럼 실행 파일을 내려받는 도구에서도 같은 일이 생길 수 있다.
  - 막히면 이 보호 기능을 끄거나 우회하지 말고, 무엇이 막혔는지 사용자에게 알리고 대안(같은 도구의 다른 배포 형태 등)을 제안해라.
- **포트**: 다른 프로젝트의 Docker 컨테이너 `cho-web-pj-web-1`이 8000 또는 8001 포트를 쓴다(2026-11-16 밤에는 8001).
  포트가 열려 있다고 이 프로젝트의 서버라고 단정하지 말고 프로세스를 확인해라. 5432와 3000은 비어 있었다.
- **Downloads 폴더는 `D:\Downloads`**에 있다. 명세 원본도 거기 있고, 이 폴더의 `SPEC.md`는 그 복사본이다.
- **Open-Meteo**: 2026-11-16 밤 이 PC에서 `api.open-meteo.com`(51.81.167.44)으로 연결이 되지 않았다(다른 사이트는 정상).
  4단계에서 날씨 도구가 실패하면 코드보다 이 접속부터 확인해라.

## API 키 (.env)

- `.env.django`는 Django판의 `.env`를 그대로 복사한 파일이다. **값을 화면에 출력하거나 다른 파일, 커밋, 로그에 옮기지 마라.**
- 새 `.env`를 만들 때 이 파일의 값을 가져오되, 변수 이름을 새 명세에 맞춘다.
  - `SECRET_KEY` → `SESSION_SECRET` (새로 만들어도 된다)
  - 새로 필요한 것: `DATABASE_URL`(docker의 postgres), `APP_ORIGIN`, `LOGFIRE_TOKEN`(선택)
  - 이름이 같은 것: `ANTHROPIC_API_KEY`, `KAKAO_REST_API_KEY`, `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET`,
    `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI`, `TOKEN_ENCRYPTION_KEY`,
    `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, `VAPID_SUBJECT`, `AGENT_MODEL_*`, `DAILY_COST_LIMIT_USD`, `TASK_MAX_TOKENS`
  - `VAPID_SUBJECT`는 Django판에서 비어 있었다. 사용자가 `mailto:주소`를 직접 넣어야 푸시가 나간다.
  - `GOOGLE_REDIRECT_URI`는 Django판 주소(`http://localhost:8000/connections/google/callback/`)다.
    새 앱의 콜백 주소가 다르면 4단계에서 Google 콘솔에 주소를 추가해야 한다(사용자 작업).
- **`.gitignore`는 `.env`만이 아니라 `.env*`를 막고 `.env.example`만 예외로 둔다.** 그래야 `.env.django`가 커밋되지 않는다.
  Django판에서도 `.env` 복사본이 규칙에 안 걸린 적이 있었다. 첫 커밋 전에 `git status`에 `.env.django`가 없는지 확인해라.
- NAVER 키는 네이버 개발자센터가 아니라 NAVER API HUB(네이버클라우드)에서 발급한 것이다.

## Django판에서 옮겨 올 것 (필요한 단계에서 읽기)

경로는 모두 `C:\Users\rbtjd\Desktop\새 폴더 (3)\CGS-guseong-AI-Agent\` 아래다.

| 새 명세 단계 | 참고할 것 | 쓰임 |
| --- | --- | --- |
| 전 단계 | `docs/SAFETY.md`, `docs/DECISIONS.md` | 이미 겪은 문제와 판단. 같은 실수를 반복하지 않기 위해 |
| 1~2단계 | `agent/prompts.py`, `agent/approvals.py`, `agent/guard.py` | 시스템 프롬프트 문구, 승인·감시 에이전트의 판단 기준 |
| 4단계 | `tools/weather.py`, `tools/places.py`, `tools/websearch.py`, `tools/gcal.py`, `tools/http.py` | 실제 계정으로 확인된 외부 API 호출 방식과 응답 처리 |
| 5단계 | `tools/` 안의 기억 도구, `memory/` | remember의 서버 검사 |
| 6단계 | `automation/services.py`, `tools/schedule_tools.py` | 예약·감시 판정, 중복 등록 거절 |
| 7단계 | `tools/gmail.py`, `tools/drafts.py` | 메일 본문 처리(4,000자, 저장 500자), 초안 |
| 화면 전체 | `배포-이어하기.txt` 3번 절, `static/src/input.css`, `templates/` | "결재" 디자인 시스템: 색, 글꼴, 승인 카드와 인장 |
| 9단계 | `docs/DEPLOY.md` | Railway 조사 내용(Django판 기준이라 서비스 구성은 다시 써야 한다) |

Django판에서 실사용으로 발견해 고친 것 중 새 판에서도 주의할 것:
- 모델이 이전 작업에서 한 일을 몰라 같은 감시를 두 번 만든 버그(이전 도구 기록을 모델에 전달 + 서버가 중복 거절).
- 승인 카드가 있는데 모델이 채팅으로 한 번 더 확인하던 문제(프롬프트에 "채팅으로 재확인하지 말라").
- 외부 사이트(카카오, 네이버, 구글 콘솔) 화면 안내가 두 번 틀렸다. 안내 전에 반드시 현재 공식 문서를 확인한다.
- Railway의 `railway.json`/`railway.toml`은 2026-12-01부터 읽지 않는다.

코드를 그대로 복사하지는 마라. Django ORM과 동기 코드라서 새 스택(비동기 SQLAlchemy, Pydantic AI 도구)에 맞게 다시 써야 한다.
가져오는 것은 호출 주소, 파라미터, 응답 처리 규칙, 문구, 판단 기준이다.

## 사용자 작업 방식

- 모든 응답은 한국어.
- 한 번에 한 단계만 하고 끝나면 멈춰서 보고한다(명세의 작업 방식).
- push는 사용자 확인 후에만 한다. push 전에 키 값과 이메일이 커밋에 없는지 검사한다.
- GitHub 저장소를 새로 만들지, 기존 저장소(CGS-guseong-AI-Agent)에 Django판을 브랜치로 남기고 올릴지는 아직 정하지 않았다.
  첫 push 전에 사용자에게 묻는다.
