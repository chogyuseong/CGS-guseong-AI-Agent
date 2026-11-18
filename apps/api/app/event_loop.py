"""이벤트 루프 선택.

psycopg 3의 비동기 연결은 Windows 기본 루프(ProactorEventLoop)에서 동작하지 않는다
(https://www.psycopg.org/psycopg3/docs/advanced/async.html). 그래서 이 프로젝트는 어디서나 SelectorEventLoop를 쓴다.
- API 서버: uvicorn 0.54는 Windows에서 리로드 없이 실행하면 ProactorEventLoop를 쓰므로
  `--loop asyncio:SelectorEventLoop`로 실행한다(uvicorn은 이 문자열을 루프 생성 함수로 그대로 쓴다).
- 테스트: tests/conftest.py의 anyio_backend 픽스처.
- 명령줄 도구: 아래 run_async.
Linux/macOS에서도 SelectorEventLoop가 기본이라 같은 설정을 그대로 써도 된다.

근거: docs/DECISIONS.md 2026-11-16(psycopg 비동기와 Windows 루프), 2026-11-17(DBOS는 lifespan 안에서 launch).
고칠 때 주의: 비동기 DB를 쓰는 새 진입점(명령줄, 스크립트)을 만들면 asyncio.run 대신 run_async를 쓴다.
그러지 않으면 Windows 기본 루프(Proactor)가 쓰여 psycopg 비동기 연결이 실패한다.
"""

import asyncio
from collections.abc import Coroutine
from typing import Any


def run_async[T](coroutine: Coroutine[Any, Any, T]) -> T:
    """명령줄 도구(app.cli, evals/run_evals.py)에서 비동기 함수를 실행하고 그 결과를 돌려준다.

    루프를 새로 만들고 끝나면 닫는다. 이미 루프가 도는 곳(서버, 테스트) 안에서는 부르지 않는다.
    """
    return asyncio.run(coroutine, loop_factory=asyncio.SelectorEventLoop)
