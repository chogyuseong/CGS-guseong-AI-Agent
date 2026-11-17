# 사용 버전

BUILD_SPEC 1.3 "버전 선택 규칙"에 따라, 실제로 설치해 테스트를 통과한 조합을 적는다.
정확한 고정 값은 lockfile(`apps/api/uv.lock`, `apps/web/pnpm-lock.yaml`, 루트 `pnpm-lock.yaml`)이 기준이다.
버전을 올리면 이 표와 확인 날짜를 고친다.

## 확인한 날짜와 결과

- 2026-11-17, 두 번째 PC(Windows 11)에서 새로 설치한 뒤 확인했다(1단계 보강 끝 기준).
  - API: pytest 400개 통과, ruff·ruff format·pyright 0건, `alembic check` 차이 없음
  - 웹: Biome 0건, tsc 통과, Vitest 263개, Playwright 37개 통과

## 도구

| 도구 | 버전 | 비고 |
|---|---|---|
| Python | 3.12.9 | `apps/api/pyproject.toml`이 3.12를 요구. 첫 PC는 3.12.10 |
| uv | 0.13.8 | `winget install astral-sh.uv` |
| Node | 24.21.0 | 첫 PC는 26.13.0. 둘 다 테스트 통과 |
| pnpm | 10.34.9 | `packageManager`로 고정. 12는 Smart App Control에 막혀 쓰지 않는다 |
| Docker | 29.7.2 | |
| PostgreSQL | 16.16 | `postgres:16` 이미지 |
| git | 2.54 | |

## API (apps/api)

| 패키지 | 버전 |
|---|---|
| fastapi | 0.163.1 |
| starlette | 1.10.0 |
| pydantic | 2.14.3 |
| pydantic-ai-slim | 2.68.0 |
| pydantic-evals | 2.68.0 |
| dbos | 3.5.0 |
| sqlalchemy | 2.1.8 |
| alembic | 1.20.1 |
| psycopg | 3.3.9 |
| httpx | 0.28.1 |
| uvicorn | 0.54.0 |
| bcrypt | 5.0.0 |
| cryptography | 50.0.2 |
| pywebpush | 2.5.0 |
| logfire | 5.6.0 |
| pytest | 9.2.0 |
| ruff | 0.17.5 |
| pyright | 1.1.414 |

## 웹 (apps/web)

| 패키지 | 버전 |
|---|---|
| next | 16.4.6 |
| react | 19.3.1 |
| typescript | 5.9.3 |
| tailwindcss | 4.3.5 |
| @tanstack/react-query | 5.107.1 |
| openapi-fetch | 0.17.0 |
| openapi-typescript | 7.13.0 |
| vitest | 5.1.0 |
| @playwright/test | 1.65.1 (설치된 Chrome 사용) |
| @biomejs/biome, @biomejs/wasm-nodejs | 2.6.8 (WASM으로 실행) |

## 통합에서 확인한 것

- FastAPI + Pydantic AI + DBOS: 서버 lifespan에서 DBOS를 launch하고, 끝나지 않은 워크플로를 복구한다.
  복구 테스트는 `tests/test_workflows.py`(재생, 중단된 INTERNAL·EXTERNAL 호출 재실행 안 함, 고아 task 재시작).
  실제 프로세스 강제 종료 절차는 `docs/DURABILITY-CHECK.md`.
- DBOS 시스템 테이블은 같은 DB의 `dbos` 스키마에 둔다. psycopg 3 하나를 앱·Alembic·DBOS가 같이 쓴다(DECISIONS 0단계).
