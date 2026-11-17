"""테스트 전용 EXTERNAL 도구 fake_external. 운영 코드(app/)에는 등록되지 않는다.

실제 외부 API 대신 "몇 번 실행되었는지"만 센다. 승인 전에는 0회, 승인 후에는 정확히 1회여야 한다.
실제 EXTERNAL 도구와 똑같이 함수 첫 줄에서 assert_approved(ctx)로 DB 승인 기록을 확인한다(두 번째 방어선).
"""

from dataclasses import dataclass, field
from typing import Annotated, Any

from pydantic import Field
from pydantic_ai import RunContext

from app.agent.approvals import assert_approved
from app.agent.deps import AgentDeps
from app.agent.plans import ExternalToolSpec
from app.agent.results import ToolResult


@dataclass
class FakeExternalCounter:
    """fake_external이 실제로 실행된 횟수와 인자."""

    calls: list[dict[str, Any]] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.calls)


COUNTER = FakeExternalCounter()
# 실행 계획 규격(2단계 보강). 연결이 필요 없고 기존 대상을 가리키지 않는다.
FAKE_EXTERNAL_SPEC = ExternalToolSpec(version="1")


async def fake_external(
    ctx: RunContext[AgentDeps], value: Annotated[int, Field(description="아무 숫자")]
) -> ToolResult:
    """테스트용 외부 도구. 앱 밖에 흔적을 남기는 행동을 흉내 낸다."""
    await assert_approved(ctx)
    COUNTER.calls.append({"value": value, "tool_call_id": ctx.tool_call_id})
    return ToolResult.success({"value": value})


async def preview_fake_external(deps: AgentDeps, args: dict[str, Any]) -> str:
    return f"가짜 외부 도구를 값 {args.get('value')}로 실행합니다."
