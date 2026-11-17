# 배포 절차 (Railway, 선택지 A)

선택 근거와 다른 선택지 비교는 docs/DEPLOY-OPTIONS.md. 이 문서는 **실행 순서**다.
2026-11-17 기준: 배포 전 코드 변경은 끝났고, **실제 배포는 아직 하지 않았다**(Railway 계정 로그인·결제·도메인은 사용자가 해야 한다).
이 PC에는 Railway CLI가 없고 로그인 정보도 없다. 아래 대시보드 절차로 진행한다(메뉴 이름은 배포 때 공식 문서로 다시 확인).

## 0. 배포 전 코드 변경 (끝남)

| 항목 | 어디 | 내용 |
| --- | --- | --- |
| API 주소 환경변수 | `apps/web/next.config.ts` | 빌드 환경변수 `API_ORIGIN`(기본 `http://localhost:8000`). 바꾸면 다시 빌드 |
| 배포용 시작 명령 | `apps/api/start.sh` | `--reload` 없음, `--no-access-log`(콜백 쿼리 로그 방지), `API_HOST`(기본 `::`), `PORT` |
| DATABASE_URL 형식 | `apps/api/app/config.py` | `postgresql://`·`postgres://`를 `postgresql+psycopg://`로 바꾼다(tests/test_config_deploy.py) |
| 로그인 시도 제한 | `apps/api/app/routers/auth.py` | 이미 있음(이메일별 실패 횟수 제한, 429) |
| 오래된 실행 기록 정리 | `apps/api/app/automation/retention.py` | 5단계 보강: DBOS 기록 포함 7일, 영수증 90일 |

## 1. Railway 프로젝트 (사용자)

1. Railway 가입, Hobby 플랜(유료). Workspace Usage에서 사용량 한도(예: 알림 $8, 하드 $15)를 먼저 건다.
2. 새 프로젝트 → GitHub 저장소 `chogyuseong/CGS-guseong-AI-Agent`(main) 연결.
3. 서비스 3개를 만든다.
   - **Postgres**: Railway PostgreSQL 템플릿.
   - **api**: 같은 저장소, Root Directory `apps/api`. 공개 도메인은 만들지 않는다(사설망만).
   - **web**: 같은 저장소, Root Directory `apps/web`. 공개 도메인을 만든다(이 주소가 `https://화면도메인`).

## 2. api 서비스 설정

- 빌드: `apps/api/uv.lock`을 보고 uv로 설치하는지 확인한다(안 되면 Build Command `uv sync --frozen`).
- Pre-deploy Command: `uv run python -m alembic upgrade head`
- Start Command: `sh start.sh`
- 재시작 정책 Always, 복제본 1개(자동 확장 끔). 새 버전과 옛 버전이 겹치지 않게 하는 설정이 있으면 켠다.
- Variables(값은 대시보드에만, 저장소·채팅에 적지 않는다):
  `DATABASE_URL`(Postgres 서비스 참조), `SESSION_SECRET`(새 값), `APP_ORIGIN=https://화면도메인`, `APP_ENV=production`,
  `ANTHROPIC_API_KEY`, `KAKAO_REST_API_KEY`, `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`,
  `GOOGLE_REDIRECT_URI=https://화면도메인/api/connections/google/callback`, `TOKEN_ENCRYPTION_KEY`(새 값, 바꾸지 않음),
  `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, `VAPID_SUBJECT`(mailto:), 선택 `DAILY_COST_LIMIT_USD`, `EXECUTION_RETENTION_DAYS`,
  `RECEIPT_RETENTION_DAYS`, `TELEMETRY_ENABLED`(true일 때만 전송) + `LOGFIRE_TOKEN`.
- 사설망 주소: `api.railway.internal`. 포트는 `PORT` 변수로 고정한다(예: `PORT=8080`).

## 3. web 서비스 설정

- Build Command: `pnpm install --frozen-lockfile && pnpm build`, Start Command: `pnpm start`
- Variables: `API_ORIGIN=http://api.railway.internal:8080`(빌드 때 읽는다)
- 공개 도메인 생성 → 그 주소를 api의 `APP_ORIGIN`·`GOOGLE_REDIRECT_URI`에 넣고 api를 다시 배포한다.

## 4. 배포 뒤 확인 (사용자, 순서대로)

1. 계정 만들기: api 서비스 셸에서 `uv run python -m app.cli create-user --email 이메일`(비밀번호는 프롬프트).
2. 화면 주소에서 로그인(POST가 403이면 Origin이 바뀐 것 → `APP_ORIGIN` 확인).
3. Google Cloud 콘솔: 승인된 리디렉션 URI에 `https://화면도메인/api/connections/google/callback` 추가 → 연결 화면에서 다시 연결.
4. 설정 → 알림 켜기 → `pnpm`이 없는 서버라 시험 푸시는 api 셸에서 `uv run python -m app.cli send-test-push --email 이메일`.
5. 폰에서 같은 주소로 푸시·마이크·키보드 확인(docs/PHONE-CHECK.md).
6. 자동화 → 아침 브리핑 켜기(원할 때만, 매일 모델 비용).

## 5. 되돌리기

자세한 순서(downgrade의 위험, 백업·복원, WORKFLOW_VERSION, 키 교체, 장애 대응, 7일 기록 양식)는 docs/OPERATIONS.md.

- Railway Deployments에서 이전 배포로 Rollback. 마이그레이션은 되돌리지 않는다(필요하면 `alembic downgrade -1`을 셸에서, 데이터 확인 후).
- 백업: Railway Postgres 백업 기능과 보관 기간을 배포 때 확인해 docs/DATA_POLICY.md "백업과 삭제 지연"을 고친다.
