"""서버 생존 확인. GET /api/health → 200 {"status": "ok"}.

- 로그인·DB 없이 답한다(DB가 죽어도 ok다. DB 상태 확인이 필요하면 별도 경로를 만든다).
- 부르는 곳: apps/web/e2e/global-setup.ts(e2e 전 API 서버가 떴는지 확인), 사람이 서버 확인할 때.
고칠 때 주의: 경로·응답을 바꾸면 e2e global-setup과 tests/test_health.py, gen:api.
"""

from fastapi import APIRouter

router = APIRouter(tags=["health"])


# 아래 docstring은 OpenAPI 설명이다.
@router.get("/health")
async def health() -> dict[str, str]:
    """서버가 떠 있는지 확인한다. 로그인 없이 부를 수 있다."""
    return {"status": "ok"}
