"""API 라우터. 모든 경로는 /api 아래에 붙는다(api_router).

app/main.py의 create_app이 api_router를 앱에 붙인다. 화면(Next.js)은 /api/*를 이 API로 넘기므로 브라우저 기준
같은 출처에서 부른다(세션 쿠키·Origin 검사가 그 전제).

공통 규칙(라우터마다 반복하지 않는다):
- 로그인: 경로 함수가 CurrentUser(app/security.py get_current_user)를 받으면 로그인 필수, 없으면 401.
- Origin 검사: GET/HEAD/OPTIONS 외 요청은 미들웨어(OriginCheckMiddleware)가 앱 전체에 건다(DECISIONS 2026-11-16).
  라우터에서 따로 하지 않는다. 새 라우터도 자동으로 적용된다.
- 소유자: 모든 조회·수정에 user_id 조건. 남의 리소스는 없는 것과 똑같이 404(존재 노출 방지).
- 커밋: 요청 세션(DbSession)은 스스로 커밋하지 않는다. 바꾸는 경로가 직접 commit한다(app/db.py).

고칠 때 주의: 라우터를 더하거나 prefix를 바꾸면 `pnpm --dir apps/web gen:api`로 apps/web/lib/api/schema.d.ts를
다시 만들고 tests/test_openapi.py를 돌린다.
"""

from fastapi import APIRouter

from app.routers import (
    approvals,
    auth,
    automations,
    connections,
    conversations,
    experiences,
    health,
    memories,
    push,
    tasks,
    todos,
    usage,
)

api_router = APIRouter(prefix="/api")
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(conversations.router)
api_router.include_router(tasks.router)
api_router.include_router(approvals.router)
api_router.include_router(push.router)
api_router.include_router(connections.router)
api_router.include_router(memories.router)
api_router.include_router(automations.router)
api_router.include_router(usage.router)  # 9단계 2번: 비용 화면
api_router.include_router(todos.router)  # 요구사항 밖: 할 일 체크리스트(DECISIONS 2026-11-18)
api_router.include_router(experiences.router)  # 생활 비서 D: 내 경험(원문 암호화, DECISIONS 2026-11-18)
