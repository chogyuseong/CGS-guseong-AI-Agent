"""실행 계획 만들기(2단계 보강, BUILD_SPEC 3.1~3.2).

외부 도구의 인자는 모델의 제안이다. 승인 대기가 되면 서버가 이렇게 계획을 고정한다.

    모델의 호출 인자 ─▶ canonical_args: 도구의 Pydantic 검증기로 다시 검증하고 기본값까지 채운다
                    ─▶ args_hash: 도구 이름·버전·정규화 인자를 키 순서와 무관한 JSON으로 만들어 sha256
                    ─▶ draft_plan: 도구 규격(버전, 연결이 필요한 서비스, 대상 인자)으로 연결 id·대상 id를 채운다
                    ─▶ with_target_version(4단계 보강): 기존 대상을 바꾸는 도구면 지금 대상 버전(일정 ETag)을 읽어 고정

- 승인 카드 문장(preview)과 감시 에이전트 입력도 모델의 원래 인자가 아니라 정규화한 인자로 만든다.
- 해시는 변경을 알아보는 수단이다. 권한 검사를 대신하지 않는다(실행기 검사는 executor.py의 begin_execution).
- 규격(EXTERNAL_TOOL_SPECS)에 없는 EXTERNAL 도구는 계획을 만들지 않는다(PlanRejectedError → 작업 실패).
  도구 파일이 SPECS를 만들고 toolsets.py가 이 딕셔너리에 넣는다. 도구 동작이 바뀌면 version을 올린다
  (이미 만든 계획은 옛 버전이라 실행기가 거부하고 다시 승인받게 된다).

진입점(누가 부르는지):
    draft_plan·with_target_version  runner.draft_for(승인 대기 run을 저장하기 전), tests/plan_helpers.py
    args_hash·connection_id_for     executor(실행 직전 해시·연결 비교)도 같은 함수를 쓴다
    EXTERNAL_TOOL_SPECS             toolsets.py가 채우고(gcal.SPECS), executor와
                                    routers/approvals.py(승인 카드 대상·계정)가 읽는다
    시간 창·POLICY_VERSION          approvals.py가 계획·승인 행을 저장하고 결정할 때 쓴다
이 모듈은 DB에 쓰지 않는다. 저장은 approvals.save_requests가 한다.

근거: BUILD_SPEC 3.1·3.2(실행 계획에 저장할 값), 3.4(결정 30분·실행 5분), 3.5(대상 버전), 4.2(재조회).
결정: docs/DECISIONS.md "2단계 보강"(2026-11-17 정규화·해시, 도구 규격 fail closed), "3단계 보강"(reconcile),
"4단계 보강"(2026-11-17 target_version·ETag).

고칠 때 주의:
- args_hash의 직렬화(키 정렬, 구분자, ensure_ascii, 해시 대상 칸)를 바꾸면 이미 저장된 모든 계획의 해시와 달라져
  승인된 계획이 실행 때 PermissionError가 된다. tests/test_action_plans.py가 해시 성질을 본다.
- 도구 규격의 version을 올리면 그 전 계획은 실행기가 plan_outdated(operation stale)로 거부한다. 일부러 그렇게 쓴다.
- 시간 창 값을 바꾸면 사용자에게 보이는 문구도 고친다: approvals.EXPIRED_MESSAGE("30분"), executor.refusal_for("5분").
  검사 규칙을 바꾸면 POLICY_VERSION을 올린다. 계획에 칸을 더하면 models·마이그레이션·approvals.plan_row를 함께 고친다.
- 계획 모양이 바뀌면 docs/STATE_MACHINES.md(operation 절)와 docs/DATA_POLICY.md(canonical_args 보관)도 확인한다.
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
# action_plans.policy_version에 기록만 한다(실행기는 이 값을 비교하지 않는다). 어떤 규칙으로 만든 계획인지 추적용.
POLICY_VERSION = "2026-11-17.stage2"
# 승인 결정 마감과 승인 뒤 실행 가능 시간(BUILD_SPEC 3.4 기본값).
# DECISION_WINDOW: 승인 행의 expires_at(= 계획의 decision_expires_at). 지나면 1분 검사·decide가 task를 취소한다.
# EXECUTE_WINDOW: 승인한 순간부터 approvals.execute_before. 오래된 승인으로 잊힌 행동이 실행되는 것을 막는다.
DECISION_WINDOW = timedelta(minutes=30)
EXECUTE_WINDOW = timedelta(minutes=5)


class PlanRejectedError(Exception):
    """서버가 이 호출로 실행 계획을 만들 수 없다(규격 없는 도구, 검증 실패). 메시지는 로그용 영어·한국어 혼용 가능.

    runner.finish가 잡아 승인을 만들지 않고 task를 failed로 끝낸다(fail closed: 검사할 수 없는 외부 호출은 승인 카드도
    띄우지 않는다). 메시지는 사용자에게 보이지 않는다.
    """


@dataclass(frozen=True)
class PlanRef:
    """재조회 함수에 넘기는 계획의 값(세션 밖에서도 쓸 수 있게 복사한 것).

    executor.start_reconcile이 만든다. operation_id가 외부 멱등성 키라 재조회 함수는 이 값으로 외부 기록을 찾는다.
    """

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
    not_done은 "다시 요청해도 된다"는 뜻이 되므로 멱등성 키로 결과를 확실히 조회할 수 있을 때만 돌려준다.
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
    target_version: 대상의 지금 버전을 읽는 함수(4단계 보강, with_target_version이 계획 때 부른다).
        없으면 고정하지 않는다.

    version을 올리면 옛 계획은 실행되지 않는다(executor.refusal_for의 plan_outdated, operation stale).
    """

    version: str
    provider: str | None = None
    target_arg: str | None = None
    target_label: str = "외부 서비스"
    reconcile: ReconcileFunc | None = None
    target_version: TargetVersionFunc | None = None


# 승인 카드의 계정 줄에 쓰는 서비스 이름(connections.provider → 화면 이름).
PROVIDER_LABELS = {"google": "Google"}


# 도구 이름 → 규격. 비어 있게 시작하고 toolsets.py가 import될 때 도구 파일의 SPECS로 채운다.
# 테스트는 tests/conftest.py가 fake_external 규격을 monkeypatch로 넣는다(운영 코드에 등록 금지).
EXTERNAL_TOOL_SPECS: dict[str, ExternalToolSpec] = {}


@dataclass(frozen=True)
class PlanDraft:
    """승인 요청 한 건에 대해 서버가 확정한 값. save_requests가 action_plans 행으로 저장한다.

    메모리에서만 만든다(operation planned 상태를 DB에 쓰지 않는 이유, docs/STATE_MACHINES.md). 저장 뒤에는 바꾸지 않고,
    인자가 달라지면 새 계획·새 승인이다(DECISIONS 2단계 보강).
    """

    tool_name: str
    tool_version: str
    canonical_args: dict[str, Any]
    args_hash: str
    connection_id: int | None
    target_resource_id: str | None
    target_version: str | None = None  # 4단계 보강: 계획을 만들 때 읽은 대상 버전(일정 ETag)


def spec_for(tool_name: str) -> ExternalToolSpec:
    """도구 규격. 없으면 PlanRejectedError(규격 없는 외부 도구는 실행기 검사를 할 수 없어 계획을 만들지 않는다)."""
    spec = EXTERNAL_TOOL_SPECS.get(tool_name)
    if spec is None:
        raise PlanRejectedError(f"규격이 등록되지 않은 외부 도구: {tool_name}")
    return spec


def canonical_args(toolsets: Sequence[FunctionToolset[Any]], tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
    """도구의 인자 검증기로 다시 검증하고 기본값을 채운 JSON 값. 도구가 없거나 인자가 틀리면 PlanRejectedError.

    toolsets는 그 task의 origin에 맞는 묶음이다(toolsets_for). 거기에 없는 도구는 이 작업에서 쓸 수 없는 도구다.
    도구 함수가 실제로 받을 값과 같게 만들려고 Pydantic AI가 쓰는 것과 같은 검증기(function_schema.validator)를 쓴다.
    """
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
    """{도구, 버전, 인자}를 키 정렬·공백 없는 JSON으로 만든 sha256 16진수.

    키 순서가 달라도 같은 값이고, 도구 이름·버전이 다르면 다른 값이다. 실행기가 같은 함수로 다시 계산해 비교하므로
    직렬화 방식을 바꾸면 저장된 계획이 모두 실행되지 않는다(파일 머리 "고칠 때 주의").
    """
    payload = {"tool": tool_name, "version": tool_version, "args": canonical}
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


async def connection_id_for(session: AsyncSession, user_id: int, provider: str | None) -> int | None:
    """이 사용자의 그 서비스 연결 id(사용자·서비스당 하나, UNIQUE). 연결이 필요 없거나 없으면 None.

    계획 때 값과 실행 때 값을 비교해 "승인 뒤 다른 계정으로 다시 연결"을 알아본다(계획의 connection_id는 외래 키가
    아니라 연결을 지워도 남는다, DECISIONS 2단계 보강). 읽기만 한다.
    """
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
    """모델의 호출 인자로 실행 계획 초안을 만든다. 규격 없음·쓸 수 없는 도구·인자 오류면 PlanRejectedError.

    연결이 없어도 거절하지 않는다(connection_id None). 승인 카드가 경고를 보이고 실행기가 connection_changed로 막는다.
    target_version은 비워 둔다. 대상 버전은 외부 호출이라 with_target_version에서 따로 채운다. DB는 읽기만 한다.
    """
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
    (실행기가 보지 못한 대상을 바꾸지 않도록 도구가 target_unknown으로 거부한다).

    부수 효과: 외부 조회(예: Google 일정 GET) 한 번. runner는 DB 세션을 닫은 뒤 부른다.
    예외를 삼키는 이유: 대상을 못 읽어도 승인 요청 자체는 막지 않는다(실행 때 도구가 거부한다).
    """
    spec = EXTERNAL_TOOL_SPECS.get(draft.tool_name)
    if spec is None or spec.target_version is None or draft.target_resource_id is None:
        return draft
    try:
        version = await spec.target_version(deps, draft.target_resource_id)
    except Exception:
        version = None
    return replace(draft, target_version=version)
