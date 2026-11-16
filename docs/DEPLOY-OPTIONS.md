# 배포 선택지 비교 (9단계 4번)

명세 9단계 4번: 화면과 API+PostgreSQL 호스팅 선택지의 무료 범위, 월 비용, 장단점을 비교하고 사용자가 고른다.
**이 문서는 비교만 한다. 실제 배포(계정 생성, 결제, 코드 변경)는 하지 않았다.**

- 가격과 무료 범위는 2026-11-17에 각 호스팅의 공식 가격·문서 페이지에서 확인했다(아래 "출처"). 기억에 의존한 수치는 없다.
  공식 페이지에서 확인하지 못한 것은 **(확인 못 함)**, 문서에 없어 배포 때 직접 봐야 하는 것은 **(배포 때 확인)**으로 적었다.
- 월 비용은 1인 사용 기준 **추정**이다. 실제 RAM·CPU 사용량은 측정한 적이 없다(배포 후 사용량 화면으로 고친다).
- Anthropic API 요금은 어느 선택지든 따로 나간다(앱의 하루 상한 `DAILY_COST_LIMIT_USD` 기본 $1).

## 권장안: Railway 한 프로젝트에 화면·API·PostgreSQL 모두 (선택지 A)

| 이유 | 설명 |
| --- | --- |
| API를 인터넷에 열지 않아도 된다 | 지금 구조는 "브라우저는 화면만 보고, API는 127.0.0.1에만 열림"이다. Railway는 같은 프로젝트 서비스끼리 사설망(`서비스이름.railway.internal`)으로 통하므로 화면(Next.js)만 공개 도메인을 받고 API·DB는 공개하지 않을 수 있다. 화면을 Vercel에 두면 Vercel이 API를 부를 수 있게 API를 공개 주소로 열어야 한다 |
| 같은 출처가 그대로 유지된다 | 지금처럼 Next.js rewrites가 `/api/*`를 API로 넘긴다. 쿠키·Origin 검사·Google 콜백 주소가 화면 도메인 하나로 정리되고 CORS를 열 필요가 없다 |
| 항상 켜져 있다 | 유료(Hobby) 플랜은 잠들지 않는다(앱 잠자기 Serverless는 켜야만 동작). DBOS의 1분 검사·복구에 맞는다 |
| 한 곳, 한 청구서 | PostgreSQL도 같은 프로젝트의 서비스다. 예상 월 $5~11(추정) |
| 조사가 이미 있다 | Django판이 Railway 배포를 준비했다(`git show origin/django:docs/DEPLOY.md`). 메뉴 경로·Pre-deploy·도메인·사용량 한도 안내를 다시 쓸 수 있다(서비스 구성은 이 앱에 맞게 다시 쓴다) |

단점: 사용량 과금이라 금액이 고정이 아니다(사용량 한도로 막는다). Free 플랜($1 크레딧)으로는 세 서비스를 한 달 켜 둘 수 없고 재시작 정책 `Always`도 못 쓴다.
`railway.json`/`railway.toml`(Config as Code)은 2026-12-01부터 읽지 않으므로 설정은 대시보드에서 넣는다.

## 이 앱이 호스팅에 요구하는 것

| 조건 | 왜 | 맞지 않는 것 |
| --- | --- | --- |
| **API 프로세스가 항상 켜져 있어야 한다** | DBOS 예약 실행 `automation-check`가 API 프로세스 안에서 매분 돈다(예약 작업, 조건 감시, 아침 브리핑 07:30, 승인 30분 만료 정리). 서버가 죽었다 켜질 때 끝나지 않은 워크플로를 복구하는 것도 이 프로세스다. 요청이 없을 때 잠들면 예약·감시·브리핑이 멈춘다 | 요청이 없으면 잠드는 무료 플랜(Render Free: 15분), 서버리스 함수(Vercel Functions 등에 API를 올리는 것), 자동 정지 설정(Fly.io `auto_stop_machines`, Railway Serverless) |
| **API 프로세스는 하나** | 전송 제한이 프로세스 메모리에 있고, DBOS 복구는 같은 executor·같은 `application_version`을 전제한다(`docs/SAFETY.md` 알려진 한계) | 자동 확장, 복제본 2개 이상 |
| **HTTPS** | 웹 푸시(서비스 워커)와 음성 입력(마이크 권한)은 보안 출처에서만 동작한다. `APP_ORIGIN`이 https면 세션 쿠키에 Secure가 자동으로 붙는다(`app/config.py` `cookie_secure`). Google OAuth 리디렉션 URI도 localhost 밖에서는 https여야 한다 | 셋 다 기본 도메인에 자동 TLS를 준다(문제없음) |
| **PostgreSQL 하나** | 앱 테이블(public)과 DBOS 시스템 테이블(dbos 스키마)이 같은 DB. 별도 Redis·워커 없음 | 없음 |
| **장기 실행 요청이 짧다** | 메시지 전송·승인 API는 워크플로를 시작만 하고 바로 응답(202/200)하고 화면이 폴링한다. 요청 시간 제한에 걸릴 일이 적다 | 없음 |

## 조합 비교

| | A. Railway 한 곳 (권장) | B. Vercel + Railway | C. Vercel + Render | (참고) D. Vercel + Fly.io |
| --- | --- | --- | --- | --- |
| 화면 | Railway 서비스(`next start`) | Vercel Hobby(무료) | Vercel Hobby(무료) | Vercel Hobby(무료) |
| API | Railway 서비스, **공개 안 함**(사설망) | Railway 서비스, 공개 도메인 필요 | Render 웹 서비스 유료 인스턴스 | Fly Machine, 자동 정지 끔 |
| PostgreSQL | Railway PostgreSQL 서비스 | Railway PostgreSQL 서비스 | Render Postgres 유료 | Fly Managed Postgres 또는 직접 운영 |
| 무료로 가능? | 아니오(Free $1/월로는 부족) | 아니오(같음) | 아니오(Free 웹은 15분 뒤 잠들고 Free DB는 30일 뒤 만료) | 아니오(새 조직은 무료 범위 없음, $1 체험 7일) |
| 예상 월 비용(추정) | **$5~11** | **$5~7** (Vercel $0) | **약 $13.3** (API $7 + DB $6 + 저장 1GB $0.30) | **약 $42~45** (Managed Postgres $38 + Machine $3.69~6.70). DB를 직접 운영하면 약 $8~11 |
| 항상 켜짐 | 유료 플랜 기본(Serverless 끔) | 같음 | 유료 인스턴스는 잠들지 않음 | `auto_stop_machines` 끄고 최소 1대 |
| 같은 출처 | 지금 그대로(rewrites → 사설망 API) | Vercel rewrites → API 공개 주소 | Vercel rewrites → Render 주소 | Vercel rewrites → Fly 주소 |
| HTTPS | `*.up.railway.app` 자동 | Vercel 도메인 자동 | Vercel 도메인 자동 | Vercel 도메인 자동 |
| 장점 | API·DB 비공개, 한 곳, 가장 지금 구조와 같음 | Next.js 호스팅은 Vercel이 가장 손이 덜 감. 화면 비용 0 | 고정 요금이라 예측 쉬움 | 리전 선택 폭이 넓음 |
| 단점 | 사용량 과금, Next.js 빌드·실행도 직접 설정 | API가 인터넷에 열림, 청구서·대시보드 두 곳, Vercel 쪽 로그·헤더 전달을 따로 확인 | 2곳 관리, 저장소 증설은 GB당 과금, 대역폭 5GB 포함 뒤 GB당 $0.15 | Managed Postgres가 비쌈. 직접 운영하면 백업·업그레이드를 내가 함 |

D는 비용과 운영 부담 때문에 권하지 않는다(비교용으로만 남김).

### 가격 근거 (2026-11-17 확인)

| 호스팅 | 확인한 값 |
| --- | --- |
| Railway | Free $0(월 $1 크레딧, 서비스당 RAM 0.5GB·vCPU 1·볼륨 0.5GB), **Hobby $5/월(사용량 $5 포함)**, Pro $20/월. RAM $10/GB·월, CPU $20/vCPU·월, 볼륨 $0.15/GB·월, 송신 $0.05/GB. 포함 사용량은 매달 초기화되고 쌓이지 않는다. Free·체험 플랜은 재시작 정책 `Always`를 쓸 수 없다 |
| Vercel | Hobby 무료: Fast Data Transfer 100GB, 함수 호출 100만, Active CPU 4시간, 초과하면 추가 구매 불가(30일 기다림). Pro $20/월(개발자 좌석당). Hobby의 상업적 사용 제한 문구는 이번에 열어 본 페이지에서 찾지 못했다 **(확인 못 함)** — 개인 사용이라 해당 없다고 본다 |
| Render | 워크스페이스 Hobby $0(대역폭 5GB 포함, 초과 $0.15/GB), Pro $25/월. 서비스 Free(512MB, 15분 무요청이면 잠들고 다시 깨는 데 약 1분), **0.5c-512mb $7/월**, 1c-2g $25/월. Postgres Free(256MB, 저장 1GB, **30일 뒤 만료**, 14일 유예 뒤 삭제), **0.1c-256mb $6/월**, 0.5c-1g $19/월, 저장 $0.30/GB. 백그라운드 워커는 무료 없음 |
| Fly.io | 새 조직은 무료 범위 없음($1 체험 크레딧, 7일). shared-cpu-1x 256MB $2.19, **512MB $3.69**, 1GB $6.70(한 달 계속 켰을 때). **Managed Postgres Basic $38/월**. 볼륨 $0.15/GB·월, 전용 IPv4 $2/월(공유 IPv4 무료) |

### 월 비용 추정 방법 (A, B)

- A: 화면(Next.js) 0.15~0.3GB + API(FastAPI·Pydantic AI·DBOS·Logfire) 0.2~0.35GB + PostgreSQL 0.1~0.2GB ≈ 0.45~0.85GB → RAM $4.5~8.5,
  CPU는 1분 검사 때문에 늘 조금씩 쓴다고 보고 $0.5~2 → 사용량 $5~10.5. Hobby $5에 $5가 포함되므로 청구는 약 **$5~11**.
- B: API + PostgreSQL만 ≈ 0.3~0.55GB → RAM $3~5.5 + CPU → 포함 $5 안팎, 청구 약 **$5~7**.
- 메모리 수치는 측정값이 아니다. Django판 문서의 추정(web 0.2 + worker 0.1 + Postgres 0.15GB)을 참고해 이 앱 구성으로 다시 잡았다.

## 공통으로 해야 하는 것 (어느 선택지든)

### 화면과 API의 출처, 쿠키, Origin 검사

- 브라우저는 **화면 도메인 하나**만 보게 한다. 화면의 `/api/*`를 Next.js rewrites가 API로 넘기면 쿠키는 화면 도메인의 1st-party 쿠키가 되고,
  상태를 바꾸는 요청의 `Origin`은 화면 도메인이다. 그래서 `APP_ORIGIN`은 **`https://화면도메인`**(끝에 `/` 없음)이다.
- 화면과 API를 서로 다른 출처로 직접 부르게 바꾸면(rewrites 없이) CORS를 열고, 쿠키를 `SameSite=None; Secure`로 바꾸고, Origin 검사 대상도
  바꿔야 한다. 지금 코드(`app/security.py`: SameSite=Lax, `APP_ORIGIN` 한 개와 정확히 비교)와 맞지 않으므로 **rewrites로 같은 출처를 유지하는 것을 전제로 한다.**
- 프록시가 `Origin`·`Cookie` 헤더를 그대로 API에 넘기는지는 배포한 뒤 로그인(POST)으로 확인한다 **(배포 때 확인)**. 403이면 Origin이 바뀐 것이다.
- `APP_ORIGIN`이 https라 Secure 쿠키가 자동으로 켜진다. 로그인 시도 횟수 제한이 아직 없으므로 인터넷에 열기 전에 계정 비밀번호를 길게 만든다.

### 배포 전에 필요한 코드 변경 (아직 하지 않음, 배포처를 고른 뒤 작업)

| 무엇 | 지금 | 바꿀 것 |
| --- | --- | --- |
| rewrites의 API 주소 | `apps/web/next.config.ts`의 `API_ORIGIN = "http://localhost:8000"` 고정 | 환경변수로 받기(A: `http://api.railway.internal:포트`, B~D: API 공개 주소). Next.js rewrites는 빌드 때 값을 읽으므로 빌드 환경변수로 넣는다 |
| API 시작 명령 | 루트 `package.json`의 `api`는 개발용(`--host 127.0.0.1 --port 8000 --reload`) | 배포용: `uv run python -m uvicorn app.main:create_app --factory --host <호스트> --port $PORT --no-access-log`(`--reload` 없음). 호스트는 A의 사설망이면 `::`, 공개면 `0.0.0.0` **(Railway 사설망의 IPv4/IPv6 바인딩은 배포 때 확인)** |
| 접속 로그 | uvicorn 기본 접속 로그는 경로와 **쿼리 문자열**을 남긴다 | Google 콜백 주소의 `code`·`state`가 로그에 남지 않게 `--no-access-log`(Django판도 같은 이유로 접속 로그를 껐다). B~D는 Vercel 런타임 로그에 rewrites 요청의 쿼리가 남는지도 확인 **(확인 못 함)** |
| `DATABASE_URL` 형식 | 코드는 `postgresql+psycopg://`를 그대로 쓴다(`app/db.py`, DBOS도 같은 값) | 호스팅이 주는 `postgresql://…`를 그대로 넣으면 SQLAlchemy가 설치되지 않은 psycopg2를 찾는다. 값을 `postgresql+psycopg://`로 바꿔 넣거나(Railway는 `PGUSER`·`PGPASSWORD`·`PGHOST`·`PGPORT`·`PGDATABASE` 참조로 조립, 참조 조립 문법은 배포 때 확인) 코드에서 바꾼다 |
| 배포 중 프로세스 겹침 | API는 하나만 떠야 한다 | 새 버전이 뜨는 동안 옛 프로세스와 잠깐 겹치는지, 겹칠 때 DBOS 복구가 같은 워크플로를 잡는지 **(배포 때 확인)**. 겹치면 "옛 프로세스를 먼저 내리는" 배포 설정을 쓴다 |

### 환경변수 넣는 곳

| 서비스 | 변수 |
| --- | --- |
| API | `DATABASE_URL`, `SESSION_SECRET`(로컬과 다른 새 값), `APP_ORIGIN`(`https://화면도메인`), `ANTHROPIC_API_KEY`, `KAKAO_REST_API_KEY`, `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI`, `TOKEN_ENCRYPTION_KEY`(새 값, 한 번 정하면 바꾸지 않음), `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, `VAPID_SUBJECT`, 선택: `AGENT_MODEL_*`, `DAILY_COST_LIMIT_USD`, `TASK_MAX_REQUESTS`, `TASK_MAX_TOKENS`, `LOGFIRE_TOKEN` |
| 화면 | rewrites의 API 주소(위 코드 변경 후 정할 이름) |

- 넣는 곳: Railway는 서비스의 Variables(공통 값은 Shared Variables), Vercel은 Project Settings의 Environment Variables, Render는 서비스의 Environment.
- 로컬 `.env`를 통째로 붙여 넣지 않는다(`APP_ORIGIN`·`GOOGLE_REDIRECT_URI`가 localhost이고 로컬 DB 주소가 들어 있다). 값은 호스팅 화면에만 넣고 저장소·문서·채팅에 옮기지 않는다.
- `SESSION_SECRET`을 바꾸면 로그인이 풀리고, `TOKEN_ENCRYPTION_KEY`를 바꾸면 Google을 다시 연결해야 하고, VAPID 키를 바꾸면 기기마다 알림을 다시 켠다.
  만드는 법은 `.env.example`과 `docs/PUSH-CHECK.md`.
- 설정은 `.env` 파일이 없어도 환경변수를 읽는다(pydantic-settings, `app/config.py`).

### Google OAuth 리디렉션 URI

1. Google Cloud 콘솔의 OAuth 클라이언트 → 승인된 리디렉션 URI에 **`https://화면도메인/api/connections/google/callback`**를 추가한다
   (끝에 `/` 없음, 글자 하나까지 같아야 함. localhost 주소는 남겨 둬도 된다).
2. API의 `GOOGLE_REDIRECT_URI`에 같은 값을 넣는다. 화면 도메인을 거쳐야 세션 쿠키(state·PKCE 검증)가 함께 온다(`docs/TOOLS.md`).
3. 앱이 '테스트' 상태면 테스트 사용자에 본인 계정이 있어야 하고 7일 뒤 다시 연결해야 할 수 있는 것은 배포 후에도 같다.
4. 배포 주소에서 Google 연결과 '메일 읽기 권한 추가'를 다시 한다(로컬 연결은 따라오지 않는다).

### 웹 푸시

- `VAPID_SUBJECT`(`mailto:주소`)를 채워야 실제로 나간다. 메일 주소는 저장소·문서에 적지 않고 호스팅 변수에만 넣는다.
- 구독은 출처마다 따로라 배포 주소에서 '알림 켜기'를 다시 누른다. 아이폰은 Safari에서 홈 화면에 추가한 앱에서만 받는다.

### 마이그레이션과 계정

- 마이그레이션: 새 버전이 뜨기 전에 한 번 `apps/api`에서 `uv run python -m alembic upgrade head`. Railway는 Pre-deploy Command(실패하면 새 버전을 띄우지 않음),
  Render는 Pre-Deploy Command, Fly는 `release_command`에 둔다(Render·Fly 쪽 기능 이름과 조건은 이번에 공식 문서로 다시 보지 않았다 **(확인 못 함)**).
  각 플랫폼 빌더가 `apps/api/uv.lock`을 보고 uv로 설치하는지 **(배포 때 확인)**.
- DBOS `dbos` 스키마는 첫 시작 때 DBOS가 만든다(마이그레이션 대상 아님).
- 계정: 회원가입이 없으므로 배포 컨테이너 안에서 `uv run python -m app.cli create-user --email 이메일`(비밀번호는 프롬프트). Railway는 `railway ssh`(Django판 문서 8절 참고).
- 데이터는 옮기지 않고 새로 시작한다(개발 데이터뿐).

### 비용 안전장치

- Railway: Workspace Usage → 사용량 한도(메일 알림 금액, 하드 한도에 닿으면 서비스가 꺼짐. Django판 기록상 하드 한도 최소 $10 — 배포 때 다시 확인).
- Anthropic 콘솔 월 한도 + 앱의 `DAILY_COST_LIMIT_USD`.

## 정해 주실 것

1. **배포처**: A(Railway 한 곳, 권장) / B(Vercel + Railway) / C(Vercel + Render) 중 무엇으로 할까요?
2. **API를 인터넷에 공개해도 될까요?** A는 공개하지 않습니다. B·C를 고르면 API 주소가 공개됩니다(로그인 쿠키와 Origin 검사로는 보호됨).
3. **월 예산 상한**: 호스팅 사용량 한도(메일 알림 금액, 하드 한도)를 얼마로 걸까요? (예: 알림 $8, 하드 $15)
4. **도메인**: 호스팅 기본 도메인(`*.up.railway.app`, `*.vercel.app` 등)으로 시작할까요, 개인 도메인을 붙일까요?
5. **배포 전 코드 변경**을 진행해도 될까요? (rewrites API 주소 환경변수화, 배포용 API 시작 명령과 접속 로그 끄기, `DATABASE_URL` 형식 처리)
6. **푸시 연락처**: `VAPID_SUBJECT`에 쓸 메일 주소를 직접 넣어 주시겠어요? (문서·저장소에는 적지 않습니다)
7. **Google 콘솔**: 배포 도메인이 정해지면 리디렉션 URI 추가를 직접 해 주셔야 합니다.
8. **로그인 시도 제한**: 인터넷에 열기 전에 로그인 시도 횟수 제한(지금 없음)을 먼저 만들까요?
9. **DBOS 기록 정리**: `dbos` 스키마의 오래된 워크플로 기록(요청 문장 포함)을 주기적으로 지울까요? 지운다면 며칠 보관할까요?

## 출처 (2026-11-17 확인)

- Railway 요금제·자원 가격: https://docs.railway.com/reference/pricing/plans
- Railway 재시작 정책(Free·체험은 `Always` 불가): https://docs.railway.com/deployments/restart-policy
- Railway 사설망(`서비스이름.railway.internal`): https://docs.railway.com/networking/private-networking
- Railway Config as Code 중단(2026-12-01): https://docs.railway.com/config-as-code
- Vercel 가격: https://vercel.com/pricing
- Vercel Hobby 포함 사용량: https://vercel.com/docs/plans/hobby
- Vercel 공정 사용 지침: https://vercel.com/docs/limits/fair-use-guidelines
- Render 가격: https://render.com/pricing (본문은 https://render.com/pricing.md 로 읽음)
- Render 무료 인스턴스(15분 잠들기, 무료 DB 30일 만료): https://render.com/docs/free
- Fly.io 가격: https://docs.fly.io/about/pricing
- 예전 Django판 Railway 조사(2026-11-16): `git show origin/django:docs/DEPLOY.md`
