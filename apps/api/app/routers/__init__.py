"""API 라우터. 모든 경로는 /api 아래에 붙는다(api_router)."""

from fastapi import APIRouter

from app.routers import approvals, auth, conversations, health, push, tasks

api_router = APIRouter(prefix="/api")
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(conversations.router)
api_router.include_router(tasks.router)
api_router.include_router(approvals.router)
api_router.include_router(push.router)
