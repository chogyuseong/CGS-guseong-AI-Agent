# Django판 참고 안내

START-HERE.md(2026-11-16)에서 옮겨 온 내용이다. 이후 단계에서 계속 쓴다.

## Django판은 무엇인가

- 같은 서비스의 Django판이 있다: `C:\Users\rbtjd\Desktop\새 폴더 (3)\CGS-guseong-AI-Agent`
  (GitHub: chogyuseong/CGS-guseong-AI-Agent). 0~7단계와 사용량 화면까지 동작하고 테스트 388개가 통과했다.
- 2026-11-16에 명세를 Next.js + FastAPI + Pydantic AI + DBOS판으로 바꾸고, 이 저장소에서 0단계부터 다시 만들기로 했다.
- **Django판은 읽기 전용 참고 자료다.** 수정하지 않는다. 코드를 그대로 복사하지도 않는다(Django ORM과 동기 코드라서
  비동기 SQLAlchemy와 Pydantic AI 도구에 맞게 다시 써야 한다). 가져오는 것은 호출 주소, 파라미터, 응답 처리 규칙,
  문구, 판단 기준이다.
- 앱 이름은 "마이에이전트"를 그대로 쓰고, 발급해 둔 API 키도 그대로 쓴다(`.env.django` → `.env`, 값은 출력 금지).

## 단계별로 참고할 것

경로는 모두 Django판 폴더 아래다.

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

## Django판 실사용에서 발견해 고친 문제 (새 판에서도 주의)

| 문제 | Django판의 해결 | 새 판에서 |
| --- | --- | --- |
| 모델이 이전 작업에서 한 일을 몰라 같은 감시를 두 번 만들었다 | 이전 도구 기록을 모델에 전달 + 서버가 중복 거절 | 1단계: 이전 task의 메시지 기록(도구 호출·결과 포함)을 이어받음(`app/agent/history.py`). 중복 거절은 6단계 |
| 승인 카드가 있는데 모델이 채팅으로 한 번 더 확인했다 | 프롬프트에 "채팅으로 재확인하지 말라" | 1단계: 시스템 프롬프트 마지막 줄에 반영(`app/agent/prompts.py`) |
| 외부 사이트(카카오, 네이버, 구글 콘솔) 화면 안내가 두 번 틀렸다 | - | 안내 전에 반드시 현재 공식 문서를 확인한다 |
| Railway의 `railway.json`/`railway.toml`은 2026-12-01부터 읽지 않는다 | - | 9단계 배포 조사 때 반영 |
| 감시 에이전트가 짧은 후속 메시지("승인")만 보고 원래 요청을 몰랐다 | 채팅 재확인을 없애 짧은 후속 메시지 자체를 줄임 | 2단계 설계 때 다시 본다 |

## API 키 메모

- `VAPID_SUBJECT`는 Django판에서 비어 있었다. 사용자가 `mailto:주소`를 직접 넣어야 푸시가 나간다(3단계).
- `GOOGLE_REDIRECT_URI`는 Django판 주소(`http://localhost:8000/connections/google/callback/`)다.
  새 앱의 콜백 주소가 다르면 4단계에서 Google 콘솔에 주소를 추가해야 한다(사용자 작업).
- NAVER 키는 네이버 개발자센터가 아니라 NAVER API HUB(네이버클라우드)에서 발급한 것이다.
- `AGENT_MODEL_*`는 Django판에서 공급자 접두사 없는 모델 ID였다. 1단계에서 Pydantic AI 형식(`anthropic:...`)으로 바꿨다.
