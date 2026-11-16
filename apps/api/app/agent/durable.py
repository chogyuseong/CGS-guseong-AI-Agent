"""DBOS 단계(step) 도우미. 실행기(runner.py)가 DBOS를 직접 다루지 않게 여기 모았다.

DBOS 워크플로는 서버가 죽었다 살아나면 처음부터 다시 실행된다. 이때
- 단계(step)로 감싼 함수는 다시 실행하지 않고, 처음 실행 때 DB(dbos 스키마)에 기록한 결과를 그대로 돌려준다.
- 단계가 아닌 코드(워크플로 코드)는 다시 실행된다.
그래서 "다시 실행되면 안 되는 일"(task 상태 바꾸기, 결과 저장, 푸시)은 durable_step으로 감싼다.

워크플로 밖(테스트에서 runner.run_task를 직접 부를 때)이나 이미 단계 안에서 부르면 그냥 함수를 실행한다.
"""

from collections.abc import Callable, Coroutine
from typing import Any

from dbos import DBOS


def in_workflow_code() -> bool:
    """지금 DBOS 워크플로 코드 안인가(단계 안이나 워크플로 밖이면 False)."""
    return DBOS.workflow_id is not None and DBOS.step_id is None


def current_workflow_id() -> str | None:
    return DBOS.workflow_id


async def durable_step[*Ts, R](name: str, func: Callable[[*Ts], Coroutine[Any, Any, R]], *args: *Ts) -> R:
    """func(*args)를 DBOS 단계 하나로 실행한다. 결과는 pickle로 저장되므로 pickle 가능한 값만 돌려준다.

    name은 워크플로 안에서 단계 순서를 확인하는 데 쓰인다(복구 때 순서가 다르면 DBOS가 오류를 낸다).
    """
    if not in_workflow_code():
        return await func(*args)
    return await DBOS.run_step_async({"name": name}, func, *args)
