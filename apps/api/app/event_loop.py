"""이벤트 루프 선택.

psycopg 3의 비동기 연결은 Windows 기본 루프(ProactorEventLoop)에서 동작하지 않는다
(https://www.psycopg.org/psycopg3/docs/advanced/async.html). 그래서 이 프로젝트는 어디서나 SelectorEventLoop를 쓴다.
- API 서버: uvicorn 0.54는 Windows에서 리로드 없이 실행하면 ProactorEventLoop를 쓰므로
  `--loop asyncio:SelectorEventLoop`로 실행한다(uvicorn은 이 문자열을 루프 생성 함수로 그대로 쓴다).
- 테스트: tests/conftest.py의 anyio_backend 픽스처.
- 명령줄 도구: 아래 run_async.
Linux/macOS에서도 SelectorEventLoop가 기본이라 같은 설정을 그대로 써도 된다.
"""

import asyncio
from collections.abc import Coroutine
from typing import Any


def run_async[T](coroutine: Coroutine[Any, Any, T]) -> T:
    """명령줄 도구(create-user 등)에서 비동기 함수를 실행한다."""
    return asyncio.run(coroutine, loop_factory=asyncio.SelectorEventLoop)
