"""실행 계획 만들기(2단계 보강, BUILD_SPEC 3.1~3.2).

외부 도구의 인자는 모델의 제안이다. 승인 대기가 되면 서버가 이렇게 계획을 고정한다.

    모델의 호출 인자 ─▶ canonical_args: 도구의 Pydantic 검증기로 다시 검증하고 기본값까지 채운다
                    ─▶ args_hash: 도구 이름·버전·정규화 인자를 키 순서와 무관한 JSON으로 만들어 sha256
                    ─▶ draft_plan: 도구 규격(버전, 연결이 필요한 서비스, 대상 인자)으로 연결 id·대상 id를 채운다

- 승인 카드 문장(preview)과 감시 에이전트 입력도 모델의 원래 인자가 아니라 정규화한 인자로 만든다.
- 해시는 변경을 알아보는 수단이다. 권한 검사를 대신하지 않는다(실행기 검사는 approvals.py).
- 규격(EXTERNAL_TOOL_SPECS)에 없는 EXTERNAL 도구는 계획을 만들지 않는다(PlanRejectedError → 작업 실패).
  도구 파일이 SPECS를 만들고 toolsets.py가 이 딕셔너리에 넣는다. 도구 동작이 바뀌면 version을 올린다
  (이미 만든 계획은 옛 버전이라 실행기가 거부하고 다시 승인받게 된다).
"""

import hashlib
import json
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
from datetime import timedelta
from typing import TYPE_CHECKING, Any, Literal

from pydantic import ValidationError
from pydantic_ai.toolsets import FunctionToolset
from pydantic_core import to_jsonable_python
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Connection

if TYPE_CHECKING:
    from app.agent.deps import AgentDeps

# 계획을 만들 때 적용한 서버 정책의 버전. 검사 규칙(유효 시간, 허용 도구 등)을 바꾸면 올린다.
POLICY_VERSION = "2026-11-17.stage2"
# 승인 결정 마감과 승인 뒤 실행 가능 시간(BUILD_SPEC 3.4 기본값).
DECISION_WINDOW = timedelta(minutes=30)
EXECUTE_WINDOW = timedelta(minutes=5)


class PlanRejectedError(Exception):
    """서버가 이 호출로 실행 계획을 만들 수 없다(규격 없는 도구, 검증 실패). 메시지는 로그용 영어·한국어 혼용 가능."""


@dataclass(frozen=True)
class PlanRef:
    """재조회 함수에 넘기는 계획의 값(세션 밖에서도 쓸 수 있게 복사한 것)."""

    operation_id: uuid.UUID
    user_id: int
    tool_name: str
    canonical_args: dict[str, Any]
    target_resource_id: str | None


@dataclass(frozen=True)
class ReconcileOutcome:
    """외부 서비스에 operation_id로 물어본 결과(3단계 보강, BUILD_SPEC 4.2).

    done: 처리 기록이 있다(resource_id·data로 영수증을 만든다) / not_done: 처리 기록이 없다고 확인했다
    / unknown: 확인하지 못했다(조회 실패, 판단 불가). unknown이면 성공·실패로 단정하지 않는다.
    """

    result: Literal["done", "not_done", "unknown"]
    resource_id: str | None = None
    data: Any = None
    provider: str | None = None  # 영수증에 적을 서비스 이름


type ReconcileFunc = Callable[["AgentDeps", PlanRef], Awaitable[ReconcileOutcome]]
# 4단계 보강: 기존 대상의 지금 버전(예: 일정 ETag)을 읽는 함수. 읽지 못하면 None.
type TargetVersionFunc = Callable[["AgentDeps", str], Awaitable[str | None]]


@dataclass(frozen=True)
class ExternalToolSpec:
    """EXTERNAL 도구의 서버 규격.

    version: 도구 동작(보내는 요청)의 버전. 계획에 적히고 실행 때 같아야 한다.
    provider: 실행에 필요한 연결(connections.provider). 없으면 None.
    target_arg: 기존 대상을 가리키는 인자 이름(예: update_event의 event_id). 새로 만드는 도구는 None.
    target_label: 승인 카드에 보일 "어디에 하는 일인지"(예: Google 캘린더(기본 캘린더)).
    reconcile: operation_id로 외부 결과를 다시 묻는 함수(3단계 보강). 없으면 결과를 모를 때 unknown으로 남긴다.
    """

    version: str
    provider: str | None = None
    target_arg: str | None = None
    target_label: str = "외부 서비스"
    reconcile: ReconcileFunc | None = None
    target_version: TargetVersionFunc | None = None


# 승인 카드의 계정 줄에 쓰는 서비스 이름(connections.provider → 화면 이름).
PROVIDER_LABELS = {"google": "Google"}


EXTERNAL_TOOL_SPECS: dict[str, ExternalToolSpec] = {}


@dataclass(frozen=True)
class PlanDraft:
    """승인 요청 한 건에 대해 서버가 확정한 값. save_requests가 action_plans 행으로 저장한다."""

    tool_name: str
    tool_version: str
    canonical_args: dict[str, Any]
    args_hash: str
    connection_id: int | None
    target_resource_id: str | None
    target_version: str | None = None  # 4단계 보강: 계획을 만들 때 읽은 대상 버전(일정 ETag)


def spec_for(tool_name: str) -> ExternalToolSpec:
    spec = EXTERNAL_TOOL_SPECS.get(tool_name)
    if spec is None:
        raise PlanRejectedError(f"규격이 등록되지 않은 외부 도구: {tool_name}")
    return spec


def canonical_args(toolsets: Sequence[FunctionToolset[Any]], tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
    """도구의 인자 검증기로 다시 검증하고 기본값을 채운 JSON 값. 도구가 없거나 인자가 틀리면 PlanRejectedError."""
    for toolset in toolsets:
        tool = toolset.tools.get(tool_name)
        if tool is None:
            continue
        try:
            validated = tool.function_schema.validator.validate_python(args)
        except ValidationError as error:
            raise PlanRejectedError(f"{tool_name} 인자 검증 실패") from error
        return to_jsonable_python(validated)
    raise PlanRejectedError(f"이 작업에서 쓸 수 없는 도구: {tool_name}")


def args_hash(tool_name: str, tool_version: str, canonical: dict[str, Any]) -> str:
    payload = {"tool": tool_name, "version": tool_version, "args": canonical}
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


async def connection_id_for(session: AsyncSession, user_id: int, provider: str | None) -> int | None:
    if provider is None:
        return None
    return await session.scalar(
        select(Connection.id).where(Connection.user_id == user_id, Connection.provider == provider)
    )


async def draft_plan(
    session: AsyncSession,
    *,
    user_id: int,
    toolsets: Sequence[FunctionToolset[Any]],
    tool_name: str,
    args: dict[str, Any],
) -> PlanDraft:
    spec = spec_for(tool_name)
    canonical = canonical_args(toolsets, tool_name, args)
    target = canonical.get(spec.target_arg) if spec.target_arg else None
    return PlanDraft(
        tool_name=tool_name,
        tool_version=spec.version,
        canonical_args=canonical,
        args_hash=args_hash(tool_name, spec.version, canonical),
        connection_id=await connection_id_for(session, user_id, spec.provider),
        target_resource_id=str(target) if target is not None else None,
    )


async def with_target_version(deps: "AgentDeps", draft: PlanDraft) -> PlanDraft:
    """기존 대상을 바꾸는 도구면 지금 대상 버전을 읽어 계획에 고정한다(BUILD_SPEC 3.5). 못 읽으면 None으로 둔다
    (실행기가 보지 못한 대상을 바꾸지 않도록 도구가 target_unknown으로 거부한다)."""
    spec = EXTERNAL_TOOL_SPECS.get(draft.tool_name)
    if spec is None or spec.target_version is None or draft.target_resource_id is None:
        return draft
    try:
        version = await spec.target_version(deps, draft.target_resource_id)
    except Exception:
        version = None
    return replace(draft, target_version=version)
