"""AgentDeps: 도구와 capability가 RunContext.deps로 받는 실행 정보.

Django의 request 객체처럼, 도구 함수가 "누가, 어떤 작업에서" 부르는지 알 수 있게 한다.
- db는 세션이 아니라 세션 공장(async_sessionmaker)이다. 도구와 Step 기록이 각자 짧은 트랜잭션을 열고 바로 커밋해서,
  작업이 도중에 실패해도 이미 한 일(할 일 추가, step 기록)은 남는다. 3단계에서 DBOS 단계(step)로 감쌀 때도
  "단계 하나 = 트랜잭션 하나"가 되어 잘 맞는다.
- settings: 외부 API 키(카카오, 네이버, Google)와 토큰 암호화 키를 읽는다(4단계).
  키는 SecretStr이라 로그에 찍히지 않는다.
- http: 외부 API를 부르는 httpx 클라이언트(4단계). 앱이 시작할 때 하나 만들어 연결을 재사용하고, 테스트는
  가짜 전송(MockTransport)을 쓰는 클라이언트로 바꿔 끼운다. 호출 규칙(타임아웃, 재시도, 오류 문장)은 app/tools/http.py.
- budget_scope: 모델 호출마다 예산을 잡을 때 쓰는 범위(1단계 보강, app/agent/budget.py). 키는 task:{id}.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.models.enums import TaskOrigin, UsagePurpose

if TYPE_CHECKING:
    from app.agent.budget import BudgetScope


@dataclass(frozen=True)
class AgentDeps:
    user_id: int
    task_id: int
    origin: TaskOrigin
    db: async_sessionmaker[AsyncSession]
    settings: Settings
    http: httpx.AsyncClient

    @property
    def budget_scope(self) -> "BudgetScope":
        # budget가 prompts→deps를 거쳐 이 모듈을 불러오므로, 순환을 피하려고 여기서 불러온다.
        from app.agent.budget import BudgetScope

        return BudgetScope(
            user_id=self.user_id,
            task_id=self.task_id or None,
            key_prefix=f"task:{self.task_id}",
            purpose=UsagePurpose.AGENT,
            db=self.db,
            settings=self.settings,
        )
