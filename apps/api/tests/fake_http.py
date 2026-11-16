"""외부 API 대신 쓰는 가짜 HTTP(httpx.MockTransport). 네트워크에 나가지 않는다.

테스트는 "어떤 주소로 오는 요청에 무엇을 돌려줄지"를 차례대로 등록한다(route). 같은 주소로 요청이 여러 번 오면
등록한 응답을 순서대로 쓰고, 마지막 응답을 반복한다. 응답 자리에 예외(httpx.ReadTimeout 등)를 넣으면 그 예외가 난다.
받은 요청은 requests에 모두 남아 헤더·파라미터를 확인할 수 있다.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

FIXTURES = Path(__file__).resolve().parent / "fixtures"

type Reply = httpx.Response | Exception | Callable[[httpx.Request], httpx.Response]


def fixture_json(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def json_response(status: int, body: Any) -> httpx.Response:
    return httpx.Response(status, json=body)


def fixture_response(name: str, status: int = 200) -> httpx.Response:
    return json_response(status, fixture_json(name))


@dataclass
class Route:
    method: str
    url: str  # 쿼리 문자열을 뺀 주소의 앞부분
    replies: list[Reply]
    used: int = 0

    def matches(self, request: httpx.Request) -> bool:
        address = str(request.url.copy_with(query=None))
        return request.method == self.method and address.startswith(self.url)

    def next_reply(self, request: httpx.Request) -> httpx.Response:
        reply = self.replies[min(self.used, len(self.replies) - 1)]
        self.used += 1
        if isinstance(reply, Exception):
            raise reply
        return reply(request) if callable(reply) else reply


@dataclass
class FakeHttp:
    routes: list[Route] = field(default_factory=list)
    requests: list[httpx.Request] = field(default_factory=list)

    def route(self, method: str, url: str, *replies: Reply) -> None:
        """나중에 등록한 같은 주소의 응답이 먼저 쓰이도록 앞에 넣는다(테스트가 기본값을 덮어쓸 수 있게)."""
        self.routes.insert(0, Route(method, url, list(replies)))

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        for route in self.routes:
            if route.matches(request):
                return route.next_reply(request)
        raise AssertionError(f"등록하지 않은 외부 요청: {request.method} {request.url.copy_with(query=None)}")

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handle))

    def sent(self, method: str, url: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.method == method and str(r.url.copy_with(query=None)).startswith(url)]
