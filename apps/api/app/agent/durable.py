"""DBOS 단계(step) 도우미. 실행기(runner.py)가 DBOS를 직접 다루지 않게 여기 모았다.

DBOS 워크플로는 서버가 죽었다 살아나면 처음부터 다시 실행된다. 이때
- 단계(step)로 감싼 함수는 다시 실행하지 않고, 처음 실행 때 DB(dbos 스키마)에 기록한 결과를 그대로 돌려준다.
- 단계가 아닌 코드(워크플로 코드)는 다시 실행된다.
그래서 "다시 실행되면 안 되는 일"(task 상태 바꾸기, 결과 저장, 푸시)은 durable_step으로 감싼다.

워크플로 밖(테스트에서 runner.run_task를 직접 부를 때)이나 이미 단계 안에서 부르면 그냥 함수를 실행한다.

쓰는 곳: runner(claim_task·prepare_run·finish_task·notify, claim_resume·prepare_resume), workflows(check_clock),
app/automation/check.py(1분 검사·예약·감시 단계), app/automation/retention.py(prune_records).
근거: DECISIONS 2026-11-17 "runner의 다시 하면 안 되는 일은 DBOS 단계(durable_step)로 감쌌다".

고칠 때 주의:
- 단계 이름·순서를 바꾸거나 단계를 더하고 빼면 workflows.WORKFLOW_VERSION을 올린다(끝나지 않은 옛 워크플로가
  새 코드로 복구되면 순서가 달라 DBOS가 오류를 낸다). 절차는 docs/OPERATIONS.md.
- 단계 결과는 dbos 스키마에 pickle로 직렬화돼 남는다(PAYLOAD_ENCRYPTION_KEY가 있으면 app/payload_crypto.py의
  EncryptedSerializer로 암호화, workflows.dbos_config).
  메일 본문 같은 민감 값을 단계 결과로 돌려주지 않는다(docs/DATA_POLICY.md "내구성 실행 기록").
"""

from collections.abc import Callable, Coroutine
from typing import Any

from dbos import DBOS


def in_workflow_code() -> bool:
    """지금 DBOS 워크플로 코드 안인가(단계 안이나 워크플로 밖이면 False).

    step_id도 보는 이유: 이미 단계 안이면 durable_step이 단계를 또 만들지 않고 그냥 실행하게 하려고.
    """
    return DBOS.workflow_id is not None and DBOS.step_id is None


def current_workflow_id() -> str | None:
    """지금 실행 중인 DBOS 워크플로 ID. 워크플로 밖(테스트에서 직접 부름)이면 None.

    runner의 claim 단계가 받아 tasks.workflow_id·agent_runs에 남긴다. 복구로 claim이 다시 돌 때 "내가 잡은 task인가"를
    이 값으로 알아보고(runner.reclaimed), outbox.resume_workflow_id가 다음 재개 번호를 tasks.workflow_id에서 계산한다.
    """
    return DBOS.workflow_id


async def durable_step[*Ts, R](name: str, func: Callable[[*Ts], Coroutine[Any, Any, R]], *args: *Ts) -> R:
    """func(*args)를 DBOS 단계 하나로 실행한다. 결과는 pickle로 저장되므로 pickle 가능한 값만 돌려준다.

    name은 워크플로 안에서 단계 순서를 확인하는 데 쓰인다(복구 때 순서가 다르면 DBOS가 오류를 낸다).
    복구 때는 func를 부르지 않고 기록된 결과를 돌려주므로, func 안의 DB 쓰기·푸시는 한 번만 일어난다.
    단, 단계가 끝나 기록되기 전에 서버가 죽으면 다시 실행되므로 func는 다시 실행해도 안전하게(잠금·고유 키로) 쓴다.
    """
    if not in_workflow_code():
        return await func(*args)
    return await DBOS.run_step_async({"name": name}, func, *args)
