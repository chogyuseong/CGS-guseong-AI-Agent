"""AgentDeps: 도구와 capability가 RunContext.deps로 받는 실행 정보.

Django의 request 객체처럼, 도구 함수가 "누가, 어떤 작업에서" 부르는지 알 수 있게 한다.
- db는 세션이 아니라 세션 공장(async_sessionmaker)이다. 도구와 Step 기록이 각자 짧은 트랜잭션을 열고 바로 커밋해서,
  작업이 도중에 실패해도 이미 한 일(할 일 추가, step 기록)은 남는다. 3단계에서 DBOS 단계(step)로 감쌀 때도
  "단계 하나 = 트랜잭션 하나"가 되어 잘 맞는다.
- http(외부 API 클라이언트)는 외부 도구가 생기는 4단계에서 추가한다.
"""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.enums import TaskOrigin


@dataclass(frozen=True)
class AgentDeps:
    user_id: int
    task_id: int
    origin: TaskOrigin
    db: async_sessionmaker[AsyncSession]
