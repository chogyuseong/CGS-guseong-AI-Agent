#!/bin/sh
# 배포용 API 시작 명령(docs/DEPLOY.md). 개발용 `pnpm api`와 다른 점:
# - --reload 없음, API 프로세스는 하나(DBOS 복구·전송 제한이 프로세스 하나를 전제)
# - --no-access-log: Google 콜백 주소의 code·state 같은 쿼리 문자열을 로그에 남기지 않는다
# - 호스트는 API_HOST(기본 "::" = IPv4·IPv6 모두, Railway 사설망용), 포트는 플랫폼이 주는 PORT(기본 8000)
# 마이그레이션은 시작 전 단계(Railway Pre-deploy)에서 `uv run python -m alembic upgrade head`로 따로 한다.
set -e
exec uv run python -m uvicorn app.main:create_app --factory \
  --host "${API_HOST:-::}" --port "${PORT:-8000}" --no-access-log
