from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    """서버가 떠 있는지 확인한다. 로그인 없이 부를 수 있다."""
    return {"status": "ok"}
