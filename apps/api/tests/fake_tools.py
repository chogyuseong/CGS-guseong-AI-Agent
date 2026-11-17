"""테스트 전용 EXTERNAL 도구 fake_external. 운영 코드(app/)에는 등록되지 않는다.

실제 EXTERNAL 도구와 똑같이 함수 첫 줄에서 begin_execution(ctx, 인자)으로 실행 계획을 검사하고(두 번째 방어선),
가짜 외부 서비스(tests/fake_service.py)에 멱등성 키 = operation_id로 요청을 보낸 뒤
finish_execution으로 영수증을 남긴다(2단계 보강).

- COUNTER: 도구가 외부 요청까지 간 횟수(같은 프로세스 메모리). 승인 전 0회, 승인 후 1회를 빠르게 확인할 때.
- SERVICE: 실제로 생긴 외부 효과(SQLite 파일). 재시작·중복 요청에도 효과가 몇 건인지 확인할 때.
  conftest의 fake_external_counter 픽스처가 테스트마다 새 파일로 정한다.
"""

from dataclasses import dataclass, field
from typing import Annotated, Any

import httpx
from pydantic import Field
from pydantic_ai import RunContext

from app.agent.deps import AgentDeps
from app.agent.executor import Execution, begin_execution, finish_execution
from app.agent.plans import ExternalToolSpec, PlanRef, ReconcileOutcome
from app.agent.results import ToolResult
from tests.fake_service import FakeExternalService


@dataclass
class FakeExternalCounter:
    """fake_external이 외부 요청까지 간 횟수와 인자."""

    calls: list[dict[str, Any]] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.calls)


@dataclass
class FakeServiceHolder:
    service: FakeExternalService | None = None


COUNTER = FakeExternalCounter()
HOLDER = FakeServiceHolder()
PROVIDER = "fake"


async def reconcile_fake(deps: AgentDeps, ref: PlanRef) -> ReconcileOutcome:
    """operation_id로 가짜 서비스에 결과를 묻는다(3단계 보강). 404면 처리 기록 없음, 조회 실패는 예외(→ unknown)."""
    if HOLDER.service is None:
        return ReconcileOutcome("unknown")
    async with HOLDER.service.client() as client:
        response = await client.get(f"/actions/{ref.operation_id}")
    if response.status_code == 404:
        return ReconcileOutcome("not_done")
    response.raise_for_status()
    found = response.json()
    return ReconcileOutcome("done", resource_id=str(found["id"]), data={"value": found["value"]}, provider=PROVIDER)


# 실행 계획 규격(2단계 보강). 연결이 필요 없고 기존 대상을 가리키지 않는다. 결과 재조회 함수가 있다(3단계).
FAKE_EXTERNAL_SPEC = ExternalToolSpec(version="1", target_label="가짜 외부 서비스", reconcile=reconcile_fake)


async def send_action(started: Execution, value: int) -> tuple[ToolResult, str | None]:
    """가짜 서비스에 요청한다. 응답이 없으면 반영됐는지 모르므로 unknown(자동 재시도 금지)."""
    if HOLDER.service is None:
        raise RuntimeError("fake_external_counter 픽스처 없이 fake_external을 불렀다")
    headers = {"Idempotency-Key": str(started.operation_id)}
    async with HOLDER.service.client() as client:
        try:
            response = await client.post("/actions", json={"value": value}, headers=headers)
        except httpx.HTTPError:
            return ToolResult.unknown("가짜 서비스 응답이 없어 반영 여부를 모릅니다."), None
    if response.status_code in (200, 201):
        return ToolResult.success({"value": value}), str(response.json()["id"])
    return ToolResult.failure("가짜 서비스가 요청을 거절했습니다.", error_code="rejected"), None


async def fake_external(
    ctx: RunContext[AgentDeps], value: Annotated[int, Field(description="아무 숫자")]
) -> ToolResult:
    """테스트용 외부 도구. 앱 밖에 흔적을 남기는 행동을 흉내 낸다."""
    started = await begin_execution(ctx, {"value": value})
    if isinstance(started, ToolResult):
        return started
    key = str(started.operation_id)
    COUNTER.calls.append({"value": value, "tool_call_id": ctx.tool_call_id, "idempotency_key": key})
    result, resource_id = await send_action(started, value)
    return await finish_execution(ctx.deps, started, result, provider=PROVIDER, resource_id=resource_id)


async def preview_fake_external(deps: AgentDeps, args: dict[str, Any]) -> str:
    return f"가짜 외부 도구를 값 {args.get('value')}로 실행합니다."
