"""가짜 외부 서비스(2단계 보강, BUILD_SPEC 13절 2단계). 테스트 전용.

실제 외부 API처럼 앱 밖에 흔적을 남긴다. 처리 기록은 SQLite 파일에 있어 앱 서버(테스트의 앱·워크플로)를 다시
만들어도, 이 서비스 객체를 새로 만들어도 남는다. 같은 프로세스 메모리 카운터만으로 장애 복구를 검증하지 않기 위해서다.

    POST /actions            헤더 Idempotency-Key 필수. 본문 {"value": 정수}
                             처음 보는 키 → 201 {"id", "key", "value", "duplicate": false} (효과 1건 기록)
                             이미 본 키   → 200 같은 기록 + "duplicate": true (효과를 더 만들지 않음)
                             같은 키에 다른 본문 → 409 (키 재사용 오류. 효과 없음)
    GET  /actions/{key}      결과 조회. 있으면 200 기록, 없으면 404 (3단계 결과 재조회용)

앱 코드는 이 서비스를 모른다. tests/fake_tools.py의 fake_external이 httpx.MockTransport로 이 처리기를 부른다.
네트워크·포트를 쓰지 않는다.
"""

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import httpx

BASE_URL = "https://fake-external.test"
SCHEMA = "CREATE TABLE IF NOT EXISTS actions (id INTEGER PRIMARY KEY, key TEXT UNIQUE NOT NULL, body TEXT NOT NULL)"


class FakeExternalService:
    def __init__(self, path: Path) -> None:
        self.path = path
        with self.connect() as db:
            db.execute(SCHEMA)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        """요청마다 파일을 새로 연다(서비스 재시작과 같은 조건). 끝나면 커밋하고 닫는다."""
        db = sqlite3.connect(self.path)
        try:
            yield db
            db.commit()
        finally:
            db.close()

    # ---- HTTP 처리기 ----

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "POST" and path == "/actions":
            return self.perform(request)
        if request.method == "GET" and path.startswith("/actions/"):
            found = self.lookup(path.removeprefix("/actions/"))
            return httpx.Response(200, json=found) if found else httpx.Response(404, json={"error": "not_found"})
        return httpx.Response(404, json={"error": "no_route"})

    def perform(self, request: httpx.Request) -> httpx.Response:
        key = request.headers.get("Idempotency-Key")
        if not key:
            return httpx.Response(400, json={"error": "idempotency_key_required"})
        body = json.loads(request.content or b"{}")
        existing = self.lookup(key)
        if existing is not None:
            if existing["value"] != body.get("value"):
                return httpx.Response(409, json={"error": "idempotency_key_reused"})
            return httpx.Response(200, json={**existing, "duplicate": True})
        with self.connect() as db:
            db.execute("INSERT INTO actions (key, body) VALUES (?, ?)", (key, json.dumps(body)))
        created = self.lookup(key)
        return httpx.Response(201, json={**(created or {}), "duplicate": False})

    # ---- 조회 ----

    def lookup(self, key: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("SELECT id, key, body FROM actions WHERE key = ?", (key,)).fetchone()
        if row is None:
            return None
        return {"id": row[0], "key": row[1], "value": json.loads(row[2]).get("value")}

    def effects(self) -> list[dict[str, Any]]:
        """지금까지 생긴 외부 효과(키마다 한 건)."""
        with self.connect() as db:
            rows = db.execute("SELECT key FROM actions ORDER BY id").fetchall()
        return [found for (key,) in rows if (found := self.lookup(key)) is not None]

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handle), base_url=BASE_URL)
