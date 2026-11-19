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

만드는 곳: runner.deps_for(채팅·예약 task 실행), app/automation/check.py(감시 도구 직접 실행),
테스트의 tests/conftest.py(tool_deps)와 일부 테스트 파일.

고칠 때 주의:
- 필드를 더하거나 빼면 위의 만드는 곳을 모두 고친다(frozen dataclass라 기본값이 없으면 생성이 실패한다).
- user_id는 모든 도구의 조회·수정 조건이다(AGENTS.md 도구 규약 2). 서버가 task에서 읽은 값만 넣고,
  모델 인자에서 받지 않는다.
- budget_scope의 key_prefix 모양("task:{id}")은 call_key("접두사:메시지 수")의 앞부분이다. 바꾸면 복구 때 같은
  호출이 두 번 예약된다(budget.py "고칠 때 주의"). 감시 에이전트는 runner가 이 범위의 접두사를 "guard:..."로 바꿔 쓴다.
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
    """한 run 동안 바뀌지 않는 실행 정보(frozen). 도구는 ctx.deps로 읽기만 한다.

    task_id가 0이면 task 없는 실행(감시 확인이 READ 도구를 직접 부를 때, check.py)으로 보고
    budget_scope의 task_id를 None으로 둔다. 감시에는 READ 도구만 가므로(app/automation/watchable.py) 이 값으로 충분하다.
    """

    user_id: int
    task_id: int
    origin: TaskOrigin
    db: async_sessionmaker[AsyncSession]
    settings: Settings
    http: httpx.AsyncClient
    # 2026-11-19 Google 캘린더 연결 여부. runner가 새 채팅 run을 시작할 때 한 번만 채운다(run 도중 바뀌지 않게).
    # False면 data_policy.visible_tool이 일정 쓰기 도구를 숨기고 prompts.calendar_instruction이 연결 안내를 붙인다.
    # 기본 True: 확인하지 않은 실행(재개 run, 감시·예약, 도구 직접 호출 테스트)은 예전처럼 둔다.
    calendar_connected: bool = True

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
