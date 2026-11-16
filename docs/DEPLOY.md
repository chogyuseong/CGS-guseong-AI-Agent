# Railway에 배포하기

마이에이전트를 인터넷에 올려 폰에서도 쓰는 방법이다. 위에서부터 순서대로 따라 하면 된다.

- 모든 안내는 **2026-11-16**에 공식 문서를 읽고 확인했다. 각 절 끝에 확인한 문서 주소를 적었다.
  Railway 화면은 자주 바뀐다. 버튼 이름이 다르면 그 주소의 최신 문서를 먼저 확인한다.
- **(확인 필요)** 라고 적힌 곳은 공식 문서에서 확인하지 못한 부분이다. 실제 화면에서 확인하고 이 문서를 고친다.
- 이 문서에는 키 값을 적지 않는다. 키는 Railway 화면의 Variables에만 넣는다.

## 0. 전체 그림

Railway 프로젝트 하나 안에 서비스 세 개를 만든다.

| 서비스 | 하는 일 | 시작 명령 | 공개 주소 |
| --- | --- | --- | --- |
| `web` | 화면과 API (Django + gunicorn) | 아래 5-1절 | 있음 (Generate Domain) |
| `worker` | 메시지 처리, 승인 후 이어 가기, 예약 작업·조건 감시·아침 브리핑(60초 주기), 푸시 발송 | `python manage.py run_worker` | 없음 |
| `Postgres` | 데이터베이스 | Railway가 알아서 | 없음 |

- `web`과 `worker`는 **같은 GitHub 저장소**에서 만들고, 시작 명령만 다르다.
- 둘 다 같은 환경변수(키, DB 주소)를 써야 한다. 그래서 공통 값은 **Shared Variables**에 한 번 넣고 두 서비스에 나눠 준다.
- `railway.json`, `railway.toml`(Config as Code)은 쓰지 않는다. 2026-12-01부터 읽지 않는 기능이라
  시작 명령은 전부 대시보드 화면에서 넣는다.
- 로컬 SQLite 데이터는 옮기지 않고 **새로 시작**한다(7절 주의 사항).

## 1. 가입과 요금제

1. https://railway.com 에서 GitHub 계정으로 가입한다(GitHub 연결이 필요하다).
2. 요금제는 **Hobby**를 고른다.
   - Hobby: 월 $5, 그 안에 사용량 $5가 포함된다. 넘으면 넘은 만큼 더 낸다.
   - Free: 월 $1 크레딧, 서비스당 RAM 0.5GB. 서비스 세 개를 한 달 내내 켜 두기에는 $1로 모자란다.
     또 Free/체험 계정은 재시작 정책 `Always`를 쓸 수 없다.
3. 가입 직후 **사용량 한도**를 먼저 건다(11절).

확인한 문서 (2026-11-16)
- 요금제: https://docs.railway.com/reference/pricing/plans
- 재시작 정책: https://docs.railway.com/deployments/restart-policy

## 2. 프로젝트 만들기와 GitHub 연결

시작 전에 확인: 저장소에 `static/css/app.css`(빌드된 CSS)가 커밋되어 있어야 한다. Railway에서는 Tailwind를 빌드하지 않는다.
CSS를 고쳤다면 로컬에서 빌드하고 커밋·push한 뒤 배포한다.

1. 대시보드에서 **New Project** → **GitHub repo** → 이 저장소를 고른다.
   (GitHub 계정이 연결되어 있지 않으면 연결하라는 화면이 먼저 나온다.)
2. **Add variables**를 고른다(바로 Deploy Now를 누르면 변수 없이 빌드되어 실패한다. 실패해도 괜찮다. 변수를 넣고 다시 배포하면 된다).
3. 만들어진 서비스의 이름을 `web`으로 바꾼다(서비스 Settings에서 이름 변경). 이름은 자유지만 이 문서는 `web`으로 부른다.

Railway는 Railpack이라는 빌더로 이 저장소를 자동으로 빌드한다.
- `requirements.txt`가 있으면 pip로 설치한다(`pyproject.toml`이 있어도 `requirements.txt`가 먼저다. Railpack 소스 코드로 확인).
- Python 버전은 저장소의 `.python-version` 파일(`3.12`)로 정한다. 로컬 `.venv`가 3.12.10이다.
  이 파일이 없으면 Railpack 기본값(3.13)을 쓴다.
- PostgreSQL 연결에 필요한 시스템 라이브러리(libpq)는 Railpack이 알아서 설치한다.

확인한 문서 (2026-11-16)
- 빠른 시작: https://docs.railway.com/quick-start
- Railpack Python: https://railpack.com/languages/python
- Railpack 소스(설치 방식 선택 순서): https://github.com/railwayapp/railpack/blob/main/core/providers/python/python.go

## 3. PostgreSQL 추가

1. 프로젝트 캔버스에서 **+ New**(또는 Ctrl/Cmd + K) → **Database** → **PostgreSQL**.
2. 서비스 이름이 `Postgres`인지 확인한다. 아래 변수 참조 `${{Postgres.DATABASE_URL}}`의 `Postgres`가 이 이름이다.
   이름을 바꿨다면 참조도 같은 이름으로 바꾼다.
3. PostgreSQL 서비스는 `DATABASE_URL`, `PGHOST`, `PGPORT`, `PGUSER`, `PGPASSWORD`, `PGDATABASE` 변수를 제공한다.
   이 앱은 `DATABASE_URL` 하나만 쓴다(`config/settings.py`의 `database_config`).
4. **Public Access(외부 접속)는 켜지 않는다.** 앱은 Railway 내부망으로 연결한다. 외부 접속을 켜면 공격 표면이 생기고 네트워크 요금이 붙는다.

확인한 문서 (2026-11-16)
- PostgreSQL: https://docs.railway.com/databases/postgresql

## 4. 환경변수 넣기

### 4-1. 새로 만들 키 (로컬과 다른 값을 권장)

배포용 키는 로컬 `.env`와 **다른 값**으로 새로 만드는 것을 권장한다. 로컬 PC가 털려도 배포 서버는 안전하다.
아래 명령은 **본인 PC의 터미널**에서 실행하고, 나온 값은 Railway 화면에만 붙여 넣는다.
화면 캡처나 AI 채팅, 메신저에 붙여 넣지 않는다.

```powershell
# SECRET_KEY
.venv\Scripts\python -c "from django.core.management.utils import get_random_secret_key as g; print(g())"
# TOKEN_ENCRYPTION_KEY
.venv\Scripts\python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# VAPID_PUBLIC_KEY, VAPID_PRIVATE_KEY (옵션 없이 실행하면 두 줄을 화면에 출력한다)
.venv\Scripts\python manage.py generate_vapid_keys
```

API 키(Anthropic, 카카오, NAVER, Google 클라이언트)는 로컬 `.env`와 같은 값을 써도 된다.

### 4-2. Shared Variables (web, worker 둘 다 쓰는 값)

**Project Settings → Shared Variables**에서 환경(production)을 고르고 하나씩 추가한 뒤, **Share** 버튼으로 `web`과 `worker`에 나눠 준다.
(`worker`는 5절에서 만든다. 만든 뒤 다시 와서 Share에 추가한다.)

| 이름 | 넣을 값 | 설명 |
| --- | --- | --- |
| `SECRET_KEY` | 4-1에서 만든 값 | 없으면 서버가 아예 뜨지 않는다 |
| `DEBUG` | `False` | 배포에서는 반드시 False. 쿠키가 Secure로 바뀐다 |
| `ALLOWED_HOSTS` | 6절에서 만든 도메인 (예: `my-agent.up.railway.app`) | `https://`와 끝의 `/` 없이 도메인만 |
| `CSRF_TRUSTED_ORIGINS` | `https://` + 도메인 (예: `https://my-agent.up.railway.app`) | 로그인 같은 POST 허용. `https://`를 꼭 붙인다 |
| `SECURE_PROXY_SSL_HEADER` | `True` | Railway 앞단이 붙여 주는 `X-Forwarded-Proto` 헤더를 믿는다 |
| `SECURE_SSL_REDIRECT` | `True` | http 요청을 https로. 위 값과 **반드시 함께** 켠다 |
| `SECURE_HSTS_SECONDS` | 처음엔 `3600`, 점검이 끝나면 `31536000` | 브라우저에 "https로만 접속" 기억시키기 |
| `SECURE_HSTS_INCLUDE_SUBDOMAINS` | `True` | `*.up.railway.app` 주소는 내 하위 도메인이 없어 켜도 영향이 없다. 자기 도메인을 붙일 땐 다시 판단 |
| `ANTHROPIC_API_KEY` | Anthropic 콘솔의 키 | 월 한도를 콘솔에서 먼저 걸어 둔다 |
| `AGENT_MODEL_FAST`, `AGENT_MODEL_SMART`, `AGENT_MODEL_GUARD` | 비워 두면 기본값 | 바꿀 때만 넣는다 |
| `DAILY_COST_LIMIT_USD`, `TASK_MAX_STEPS`, `TASK_MAX_TOKENS` | 비워 두면 기본값($1, 12, 60000) | 바꿀 때만 넣는다 |
| `KAKAO_REST_API_KEY` | 카카오 REST API 키 | 장소 검색, 날씨 좌표 |
| `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET` | NAVER API HUB 키 | 웹 검색 |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | Google OAuth 클라이언트 | 워커도 토큰 갱신에 쓴다 |
| `GOOGLE_REDIRECT_URI` | `https://` + 도메인 + `/connections/google/callback/` | 9절. 끝의 `/`까지 정확히 |
| `TOKEN_ENCRYPTION_KEY` | 4-1에서 만든 값 | Google 토큰 암호화. **한 번 정하면 바꾸지 않는다**(7절) |
| `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY` | 4-1에서 만든 값 | 웹 푸시. web(구독)과 worker(발송)가 **같은 쌍**을 써야 한다 |
| `VAPID_SUBJECT` | `mailto:` + 본인 메일 주소 | 10절. 비어 있으면 푸시가 나가지 않는다 |
| `PYTHONUNBUFFERED` | `1` | 워커의 `print` 로그가 버퍼에 쌓이지 않고 Railway 로그에 바로 보이게. Railpack이 기본으로 넣는지는 (확인 필요) |

- 한꺼번에 넣고 싶으면 서비스 Variables의 **Raw Editor**에 `.env` 형식으로 붙여 넣을 수 있다.
  단, 로컬 `.env`를 **그대로** 붙여 넣지 않는다(`DEBUG=True`, `localhost` 리디렉션 주소, 빈 `DATABASE_URL=`이 들어간다).
- 값을 넣으면 바로 반영되지 않고 "변경 사항"으로 모인다. 화면 위쪽의 **Deploy**를 눌러야 적용된다.
- `ALLOWED_HOSTS` 등 도메인이 들어가는 값은 6절에서 도메인을 만든 뒤 채운다.

### 4-3. 서비스별 변수 (DATABASE_URL)

`web`과 `worker` 각각의 **Variables** 탭에 아래 하나를 넣는다.

| 이름 | 값 |
| --- | --- |
| `DATABASE_URL` | `${{Postgres.DATABASE_URL}}` |

- `${{서비스이름.변수이름}}`은 다른 서비스의 변수를 참조하는 문법이다. 비밀번호가 바뀌어도 자동으로 따라간다.
- Shared Variables에 `DATABASE_URL`을 넣지 않는다(서비스 변수와 겹쳐 헷갈린다).
  Shared Variable 값 안에서 다른 서비스를 참조할 수 있는지는 **(확인 필요)**. 그래서 서비스마다 넣는 방식으로 안내한다.

확인한 문서 (2026-11-16)
- 변수, Shared Variables, 참조 문법, Raw Editor: https://docs.railway.com/guides/variables

## 5. 시작 명령

### 5-1. web 서비스

`web` 서비스 → **Settings → Deploy**에서 두 칸을 채운다.

**Pre-deploy Command** (새 버전이 뜨기 전에 한 번 실행)

```
python manage.py migrate --noinput
```

**Custom Start Command**

```
python manage.py collectstatic --noinput && gunicorn config.wsgi --bind 0.0.0.0:$PORT --workers 2 --threads 4
```

왜 이렇게 나눴나:
- **마이그레이션은 Pre-deploy**에서 한다. 실패하면 새 버전이 뜨지 않고 이전 버전이 계속 돈다(망가진 DB로 뜨는 것을 막는다).
  Pre-deploy는 앱과 같은 환경변수와 내부망을 쓰는 별도 컨테이너에서 돈다.
- **collectstatic은 Start Command**에서 한다. Pre-deploy 컨테이너에서 만든 파일은 **남지 않는다**(공식 문서: 파일 시스템 변경이 유지되지 않음).
  collectstatic은 `staticfiles/` 폴더에 파일을 만들어야 하므로 앱이 실제로 뜨는 컨테이너에서 실행한다.
  이걸 빼먹으면 DEBUG=False에서 모든 화면이 500 오류가 난다(13절).
- Railpack 빌드의 시작 명령은 셸에서 실행되므로 `$PORT`와 `&&`가 그대로 동작한다.
- Railway가 `PORT` 변수를 넣어 주고, 앱은 `0.0.0.0`에서 그 포트를 들어야 한다. gunicorn은 `PORT`가 있으면 원래 `0.0.0.0:$PORT`를
  기본으로 쓰지만(gunicorn 설정 코드로 확인), 읽는 사람이 알 수 있게 `--bind`를 적어 두었다.
- `--workers 2 --threads 4`: 진행 표시가 1초마다 요청을 보내고, 승인 직후의 외부 호출(최대 10초대)이 요청 안에서 돈다.
  한 요청이 다른 요청을 막지 않게 프로세스 2개 × 스레드 4개로 둔다. RAM을 줄이려면 `--workers 1`로 낮춘다.
- **gunicorn 접속 로그(`--access-logfile`)는 일부러 켜지 않는다.** Google 연결 콜백 주소에 인가 코드가 쿼리로 붙어 오는데,
  접속 로그를 켜면 그 주소가 로그에 그대로 남는다.
- Healthcheck Path는 **비워 둔다.** Railway 헬스체크는 `healthcheck.railway.app` 호스트로 요청하고 2xx만 성공으로 보는데,
  이 앱은 그 호스트를 허용하지 않고(ALLOWED_HOSTS) 첫 화면이 로그인으로 리디렉션된다. 헬스체크는 기본으로 꺼져 있다.

### 5-2. worker 서비스

1. 프로젝트 캔버스에서 **Create(+ New) → Empty service**로 빈 서비스를 만들고 이름을 `worker`로 바꾼다.
2. `worker` → **Settings → Source**에서 같은 GitHub 저장소를 연결한다. **(확인 필요: 저장소 연결 칸의 정확한 이름)**
3. `worker` → **Settings → Deploy → Custom Start Command**:

```
python manage.py run_worker
```

4. Pre-deploy Command는 **비워 둔다.** 마이그레이션은 `web` 한 곳에서만 한다(둘이 동시에 하면 충돌할 수 있다).
5. `worker`에는 **도메인을 만들지 않는다**(웹 요청을 받지 않는다).
6. 4-2의 Shared Variables를 `worker`에도 Share하고, 4-3의 `DATABASE_URL`도 넣는다.
7. **Serverless(앱 잠자기)는 켜지 않는다.** 워커는 60초마다 스스로 일해야 하는데, 잠들면 예약 작업과 아침 브리핑이 멈춘다.

알아 둘 것:
- 시작 명령을 넣지 않으면 Railpack이 Django를 알아보고 `migrate && gunicorn ...`을 기본으로 돌린다. 그러면 워커가 아니라 웹 서버가 하나 더 뜬다.
  `worker`에는 반드시 시작 명령을 넣는다.
- 워커는 종료 신호(SIGTERM)를 받으면 처리 중인 작업을 끝내고 멈춘다. 새 버전 배포 중 잠깐 두 워커가 겹쳐도
  PostgreSQL에서는 한 Task를 한 워커만 잡는다(`select_for_update(skip_locked=True)` + 조건부 UPDATE).
- 저장소에 push하면 `web`과 `worker`가 각각 다시 배포된다. 새 마이그레이션이 있는 버전이면 `worker`가 `web`의
  마이그레이션보다 먼저 뜰 수 있다. 워커 로그에 DB 오류가 보이면 `web` 배포가 끝난 뒤 `worker`를 한 번 다시 배포(Redeploy)한다.
- 워커가 죽으면 재시작 정책(기본: 실패 시 최대 10번)에 따라 다시 뜬다.

확인한 문서 (2026-11-16)
- Pre-deploy Command (메뉴 경로 Settings → Deploy → Pre-deploy Command, 파일 시스템 비유지): https://docs.railway.com/deployments/pre-deploy-command ,
  https://railway.com/guides/nextjs (2026-11-05 갱신본에서 메뉴 경로 확인)
- Start Command (Railpack은 셸에서 실행): https://docs.railway.com/guides/start-command
- PORT와 0.0.0.0, gunicorn 기본값: https://docs.railway.com/networking/troubleshooting/application-failed-to-respond
- Django 가이드(Empty service, Custom Start Command, WhiteNoise): https://railway.com/guides/django
- 헬스체크: https://docs.railway.com/deployments/healthchecks
- Serverless: https://docs.railway.com/deployments/serverless
- 재시작 정책: https://docs.railway.com/deployments/restart-policy

## 6. 도메인 만들기

1. `web` → **Settings → Networking → Public Networking → Generate Domain**.
2. `무언가.up.railway.app` 주소가 생긴다. 이 주소를 4-2의 `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, `GOOGLE_REDIRECT_URI`에 넣고 **Deploy**.
   - 포트(target port)는 앱이 하나의 포트만 들으면 Railway가 자동으로 맞춘다. 화면에 포트를 묻는 칸이 나오면 비워 두거나 자동 값을 쓴다.
3. HTTPS 인증서는 Railway가 자동으로 만들고 갱신한다.

Railway 앞단(엣지)이 하는 일(공식 문서로 확인):
- TLS(HTTPS)를 앞단에서 풀고 앱에는 http로 넘긴다. 그래서 `SECURE_PROXY_SSL_HEADER=True`가 필요하다.
- 요청마다 `X-Forwarded-Proto` 헤더를 붙이며 값은 **항상 `https`**다. 사용자가 꾸민 값이 그대로 오지 않으므로 이 헤더를 믿어도 된다.
- 80번 포트(http)로 온 GET은 앞단이 301로 https로 돌려보낸다. 앱의 `SECURE_SSL_REDIRECT`는 같은 일을 한 번 더 하는 안전장치다.
- 요청은 데이터가 오가면 최대 15분, 5분 동안 아무 데이터가 없으면 끊긴다.
- 앞단이 원래 `Host` 헤더를 그대로 앱에 넘기는지는 문서에 없다(원래 호스트를 담은 `X-Forwarded-Host`는 붙인다) **(확인 필요)**.
  이 앱은 `Host` 헤더로 `ALLOWED_HOSTS`를 검사한다. 배포 후 로그인 화면이 400 Bad Request면 13절을 본다.

확인한 문서 (2026-11-16)
- 도메인 만들기: https://docs.railway.com/guides/public-networking
- 앞단 헤더, 리디렉션, 시간 제한: https://docs.railway.com/networking/public-networking/specs-and-limits

## 7. 데이터: 새로 시작한다

배포 서버는 **빈 PostgreSQL로 새로 시작**한다. 로컬 `db.sqlite3`의 대화, 기억, 자동화, Google 연결은 옮기지 않는다.

주의 사항:
- **`TOKEN_ENCRYPTION_KEY`는 한 번 정하면 바꾸지 않는다.** Google 토큰은 이 키로 암호화되어 DB에 저장된다.
  키를 바꾸면 저장된 토큰을 읽을 수 없어 Google을 **다시 연결**해야 한다(설정 > 외부 서비스 연결).
  잃어버려도 마찬가지다. 키는 비밀번호 관리자 같은 안전한 곳에도 적어 둔다.
- `SECRET_KEY`를 바꾸면 로그인 세션이 모두 풀린다(다시 로그인하면 된다).
- `VAPID_*` 키를 바꾸면 기존 푸시 구독이 무효가 된다. 설정 화면에서 "알림 켜기"를 다시 누른다.
- 웹 푸시 구독은 사이트 주소(도메인)마다 따로라서, 로컬에서 켠 알림은 배포 주소에 넘어오지 않는다. 배포 주소에서 다시 켠다.
- 로컬과 배포가 같은 Google 계정에 연결되어도 서로 다른 토큰이다. 한쪽에서 연결 해제를 누르면 Google 쪽 권한 철회(revoke)가
  다른 쪽 토큰에도 영향을 줄 수 있다 **(확인 필요)**.
- 나중에 정말 옮기고 싶으면 `dumpdata`/`loaddata`를 쓸 수 있지만, 암호화된 토큰은 같은 `TOKEN_ENCRYPTION_KEY`일 때만 읽힌다.
  이 문서는 옮기는 방법을 다루지 않는다.

## 8. 관리자 계정 만들기

이 앱에는 회원가입 화면이 없다. 배포 서버에서 `createsuperuser`를 한 번 실행해야 로그인할 수 있다.
Railway CLI의 `railway ssh`로 **실행 중인 web 컨테이너 안에** 들어가서 실행한다.

1. Railway CLI 설치(둘 중 하나):
   ```powershell
   npm i -g @railway/cli      # Node가 있으면
   scoop install railway      # Scoop이 있으면
   ```
2. 로그인과 프로젝트 연결(프로젝트 폴더에서):
   ```powershell
   railway login
   railway link               # 프로젝트, 환경(production), 서비스(web)를 고른다
   ```
3. 처음 `railway ssh`를 실행하면 SSH 키를 Railway에 등록하라고 묻는다. 안내대로 등록한다(Windows에 OpenSSH 클라이언트가 있어야 한다).
4. 컨테이너에 들어가서 계정을 만든다:
   ```powershell
   railway ssh
   ```
   ```bash
   python manage.py createsuperuser
   ```
   - `python`을 못 찾거나 `manage.py`가 없다고 나오면 앱 폴더로 이동한다. Railpack 이미지의 앱 폴더 위치와
     PATH는 **(확인 필요)**. `ls`로 `manage.py`가 있는 폴더를 찾는다.
   - 비밀번호는 로컬과 다른 긴 비밀번호를 쓴다. 이 계정이 인터넷에 열린 앱의 유일한 문이다.
5. `exit`로 나온다.

새 계정이 만들어지면 아침 브리핑(07:30)도 자동으로 만들어진다.

확인한 문서 (2026-11-16)
- railway ssh: https://docs.railway.com/cli/ssh
- CLI 설치, login, link: https://docs.railway.com/cli
- (참고) `railway run`은 **내 PC에서** Railway 변수를 넣고 명령을 실행한다. DB 주소가 내부망 주소라 내 PC에서는 연결되지 않을 수 있어
  여기서는 쓰지 않는다: https://docs.railway.com/cli/run

## 9. Google 연결 주소 바꾸기

Google OAuth는 등록된 리디렉션 주소로만 돌아온다. 배포 주소를 추가한다(localhost 주소는 지우지 않아도 된다. 둘 다 둘 수 있다).

1. Google Cloud 콘솔 → **Google Auth Platform → 클라이언트(Clients)** → 이 앱의 웹 클라이언트를 누른다.
2. **승인된 리디렉션 URI(Authorized redirect URIs)**에 추가:
   `https://내도메인.up.railway.app/connections/google/callback/`
   - `https`, 대소문자, **끝의 `/`까지** 정확히 같아야 한다. 다르면 `redirect_uri_mismatch` 오류가 난다.
3. 저장. 반영까지 **5분~몇 시간** 걸릴 수 있다.
4. Railway의 `GOOGLE_REDIRECT_URI`도 같은 주소로 넣고 Deploy.
5. 앱이 테스트(Testing) 상태면 **대상(Audience) → 테스트 사용자**에 내 계정이 있어야 하고, 7일마다 다시 연결해야 하는 것은 배포 후에도 같다.
6. 배포 앱에서 설정 > 외부 서비스 연결 > Google 연결. 메일 읽기를 쓰려면 '메일 읽기 권한 추가'도 다시 누른다.

확인한 문서 (2026-11-16)
- 리디렉션 URI 규칙(https 필수, 정확히 일치): https://developers.google.com/identity/protocols/oauth2/web-server
- 클라이언트 관리, 반영 시간: https://support.google.com/cloud/answer/15549257?hl=en

## 10. VAPID_SUBJECT (웹 푸시)

- `VAPID_SUBJECT`는 푸시 서비스(구글·애플 등)가 문제가 있을 때 연락할 주소다. 형식은 `mailto:` 뒤에 본인 메일 주소.
- 비어 있으면 푸시가 실제로 나가지 않는다. 메일 주소는 개인 정보라 이 문서와 저장소에는 적지 않는다. Railway 변수에만 넣는다.
- 푸시는 HTTPS에서만 동작한다. 배포 주소는 HTTPS라 폰에서도 된다.
- 아이폰은 Safari에서 **홈 화면에 추가**한 앱에서만 받는다(iOS 16.4 이상).

## 11. 예상 비용

| 항목 | 가격 (공식) | 이 앱의 대략 사용량 (추정) |
| --- | --- | --- |
| Hobby 구독 | 월 $5 (사용량 $5 포함) | 고정 |
| RAM | $10 / GB·월 | web 약 0.2GB + worker 약 0.1GB + Postgres 약 0.15GB ≈ 0.45GB → 약 $4.5 |
| CPU | $20 / vCPU·월 | 대부분 쉬고 있음. 평균 0.02~0.05 vCPU 가정 → 약 $0.4~1 |
| 볼륨(DB 저장) | $0.15 / GB·월 | 1GB 미만 → $0.15 이하 |
| 네트워크 송신 | $0.05 / GB | 개인용이면 거의 0 |

- 합계 추정: **월 $5~7** (포함된 $5를 조금 넘을 수 있다). RAM 사용량은 **추정치**다. 배포 후 며칠 뒤 Usage 화면에서 실제 값을 보고 이 표를 고친다.
- RAM을 줄이려면 `web`의 `--workers 2`를 `1`로 낮춘다.
- Anthropic API 요금은 별도다(앱의 하루 상한 `DAILY_COST_LIMIT_USD` 기본 $1).
- **사용량 한도를 건다**: Workspace의 **Usage** 페이지 → **Set Usage Limits**.
  - Custom email alert: 정한 금액에 닿으면 메일만 온다(서비스는 계속 돈다). 예: $8.
  - Hard limit: 닿으면 **모든 서비스가 꺼진다.** 최소 $10. 예: $15.

확인한 문서 (2026-11-16)
- 요금: https://docs.railway.com/reference/pricing/plans
- 사용량 한도: https://docs.railway.com/pricing/cost-control

## 12. 배포 후 점검 목록

배포가 끝나면 폰과 PC에서 순서대로 확인한다.

- [ ] `web`, `worker`, `Postgres` 세 서비스가 모두 초록색(Active)이다.
- [ ] `web` 배포 로그에 Pre-deploy의 `migrate`가 성공했고, collectstatic 뒤 gunicorn이 `0.0.0.0:포트`에서 듣고 있다.
- [ ] `worker` 로그에 워커 시작 메시지가 있고 오류가 반복되지 않는다.
- [ ] `http://내도메인`으로 들어가면 `https://`로 바뀐다.
- [ ] 로그인 화면에 CSS가 적용되어 있다(글꼴, 색). 브라우저 개발자 도구에서 `/static/css/app.<해시>.css`가 200이다.
- [ ] 8절에서 만든 계정으로 로그인된다(403이 나오면 13절 CSRF).
- [ ] 채팅에 "안녕"을 보내면 답이 온다. "차례를 기다리는 중"에서 멈추면 워커 문제다(13절).
- [ ] "내일 서울 날씨 알려줘"처럼 도구를 쓰는 요청이 된다(카카오 키 확인).
- [ ] 설정 > 외부 서비스 연결에서 Google 연결 → 일정 조회 요청 → 일정 등록 요청 시 **승인 카드**가 뜨고, 승인 후 실제로 등록된다.
- [ ] 자동화: "내일 비 올 확률 60% 넘으면 알려줘"로 감시를 등록하고 자동화 화면에 보이는지 확인한다.
      "몇 분 뒤에 ~ 요약해줘" 같은 예약 작업이 정한 시각에 대화방에 결과를 남기면 워커의 60초 주기가 도는 것이다.
- [ ] 아침 브리핑: 다음 날 07:30 이후 대화방에 브리핑이 있다.
- [ ] 푸시: 설정 > **알림 켜기** → 20초 넘게 걸리는 작업이나 승인 요청에서 알림이 온다.
      아이폰은 홈 화면에 추가한 앱에서 확인한다.
- [ ] 음성: 폰(Chrome/Safari)에서 마이크 버튼 → 마이크 권한 허용 → 말한 내용이 입력창에 채워지고 자동 전송되지 않는다.
- [ ] 홈 화면에 추가했을 때 아이콘과 이름(마이에이전트)이 보인다.
- [ ] 점검이 끝나면 `SECURE_HSTS_SECONDS`를 `31536000`으로 올리고 Deploy.

## 13. 문제 해결

| 증상 | 원인과 해결 |
| --- | --- |
| "Application failed to respond" | 앱이 `0.0.0.0:$PORT`에서 듣지 않는다. `web`의 Custom Start Command를 5-1과 똑같이 넣었는지, 배포 로그에 gunicorn이 떴는지 본다 |
| 배포 로그에 `SECRET_KEY 환경변수가 없습니다` | 그 서비스에 Shared Variables가 Share되지 않았다. `web`, `worker` 둘 다 확인. Pre-deploy도 같은 변수를 쓴다 |
| 모든 화면이 500, 로그에 `Missing staticfiles manifest entry` | collectstatic이 안 돌았다. Start Command 앞부분 `python manage.py collectstatic --noinput &&`를 확인. Pre-deploy에 넣으면 결과가 남지 않는다 |
| collectstatic이 `src/tailwindcss`를 못 찾는다고 실패 | Tailwind 원본(`static/src/`)이 수집에 섞였다. `config/apps.py`의 `ProjectStaticFilesConfig`가 `INSTALLED_APPS`에 있는지 확인 |
| 로그인 화면이 400 Bad Request | `ALLOWED_HOSTS`에 도메인이 없거나 `https://`·`/`가 붙어 있다. 도메인만 넣는다. 그래도 같으면 앞단이 `Host`를 바꾸는지 확인(6절, 확인 필요) |
| 로그인 버튼을 누르면 403 (CSRF) | `CSRF_TRUSTED_ORIGINS`가 `https://도메인`과 정확히 같은지, `SECURE_PROXY_SSL_HEADER=True`인지 확인 |
| "리디렉션이 너무 많습니다" | `SECURE_SSL_REDIRECT=True`인데 `SECURE_PROXY_SSL_HEADER`가 꺼져 있다. 둘 다 True로 |
| 로그에 `relation ... does not exist` | 마이그레이션이 안 됐다. `web`의 Pre-deploy Command와 그 로그를 확인. `worker`라면 `web` 배포 뒤 `worker`를 Redeploy |
| 메시지를 보내도 "차례를 기다리는 중"에서 멈춤 | `worker`가 꺼져 있거나 시작 명령이 없다(웹 서버가 하나 더 떠 있음). `worker`의 Custom Start Command와 로그 확인. `DATABASE_URL`이 `web`과 같은지 확인 |
| Google 연결에서 `redirect_uri_mismatch` | 콘솔의 리디렉션 URI와 `GOOGLE_REDIRECT_URI`가 글자 하나까지 같은지(https, 끝의 `/`). 콘솔 저장 후 최대 몇 시간 걸릴 수 있다 |
| Google 연결이 됐는데 일정 조회가 "다시 연결" 안내 | `TOKEN_ENCRYPTION_KEY`를 바꿨거나 테스트 앱의 7일 만료다. 다시 연결 |
| 푸시가 안 온다 | `VAPID_SUBJECT`가 비었는지, `web`과 `worker`의 VAPID 키가 같은 쌍인지(Shared Variables), 알림을 배포 주소에서 다시 켰는지 확인 |
| 마이크 버튼이 안 보인다 | 브라우저가 음성 인식을 지원하지 않는다(일부 브라우저). 폰 Chrome이나 Safari로 확인 |
| 비용이 예상보다 많다 | Usage 화면에서 서비스별 RAM을 본다. `--workers 1`로 낮추고, 사용량 한도를 건다 |

## 이 배포를 위해 코드에서 바꾼 것 (2026-11-16)

- `requirements.txt`: `gunicorn`, `whitenoise`, `psycopg[binary,pool]` 추가(Railway Django 가이드).
- `.python-version`: `3.12` (Railpack이 Python 버전을 정하는 파일).
- `config/settings.py`
  - WhiteNoise 미들웨어(SecurityMiddleware 바로 다음)와 `CompressedManifestStaticFilesStorage`(파일 이름에 해시, gzip).
  - `.webmanifest`를 `application/manifest+json`으로 내보낸다(WhiteNoise 기본 형식표에 없음).
  - `CSRF_TRUSTED_ORIGINS`, `SECURE_PROXY_SSL_HEADER`, `SECURE_SSL_REDIRECT`, `SECURE_HSTS_SECONDS`,
    `SECURE_HSTS_INCLUDE_SUBDOMAINS`를 환경변수로. 기본은 전부 꺼짐이라 로컬 개발과 테스트는 그대로다.
- `config/apps.py`: collectstatic에서 Tailwind 원본 `static/src/`를 뺀다(그대로 두면 collectstatic이 실패한다).
- `config/settings_test.py`: 테스트는 collectstatic 없이 돌도록 일반 정적 파일 저장소를 쓴다.
- 서비스 워커는 계속 `/sw.js`(Django 뷰)로 나간다. 매니페스트와 서비스 워커 안의 아이콘 주소(`/static/icons/icon-192.png`)처럼
  해시 없는 주소도 WhiteNoise가 그대로 내보낸다(배포 설정으로 collectstatic 후 확인).
