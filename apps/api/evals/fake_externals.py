"""eval용 가짜 외부 API(httpx.MockTransport). 실제 카카오·네이버·Open-Meteo·Google을 부르지 않는다.

- 조회(GET)는 녹화된 응답(tests/fixtures/의 실제 녹화본)이나 사례가 정한 가짜 응답을 돌려준다.
- 쓰기(GET 밖의 모든 요청)는 보내지 않고 기록만 한 뒤 500으로 답한다. eval에서 쓰기 요청이 생기면
  NoExternalWrites 평가기가 실패로 표시한다(승인하지 않으므로 원래 생기면 안 된다).
- 등록하지 않은 주소는 404로 답하고 기록한다(네트워크로 나가지 않는다).
"""

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

import httpx

from app.tools.gcal import EVENTS_URL
from app.tools.gmail import MESSAGES_URL
from app.tools.places import KEYWORD_URL
from app.tools.weather import FORECAST_URL
from app.tools.websearch import WEBKR_URL
from evals.cases import CalendarEvent, WebResult

RECORDED = Path(__file__).resolve().parents[1] / "tests" / "fixtures"


def recorded(name: str) -> Any:
    return json.loads((RECORDED / name).read_text(encoding="utf-8"))


def google_event(event: CalendarEvent, today: date) -> dict[str, Any]:
    day = event.on_day(today).isoformat()
    return {
        "kind": "calendar#event",
        "id": event.id,
        "status": "confirmed",
        "htmlLink": f"https://calendar.google.com/calendar/event?eid={event.id}",
        "summary": event.title,
        "start": {"dateTime": f"{day}T{event.start}:00+09:00", "timeZone": "Asia/Seoul"},
        "end": {"dateTime": f"{day}T{event.end}:00+09:00", "timeZone": "Asia/Seoul"},
    }


def in_range(event: dict[str, Any], params: httpx.QueryParams) -> bool:
    start = datetime.fromisoformat(event["start"]["dateTime"])
    time_min, time_max = params.get("timeMin"), params.get("timeMax")
    if time_min and start < datetime.fromisoformat(time_min):
        return False
    return not (time_max and start >= datetime.fromisoformat(time_max))


def naver_body(results: list[WebResult]) -> dict[str, Any]:
    items = [r.model_dump() for r in results]
    return {"lastBuildDate": "", "total": len(items), "start": 1, "display": len(items), "items": items}


@dataclass
class EvalHttp:
    today: date
    events: list[CalendarEvent] = field(default_factory=list)
    web_results: list[WebResult] | None = None
    requests: list[str] = field(default_factory=list)
    writes: list[str] = field(default_factory=list)

    def handle(self, request: httpx.Request) -> httpx.Response:
        address = str(request.url.copy_with(query=None))
        self.requests.append(f"{request.method} {address}")
        if request.method != "GET":
            self.writes.append(f"{request.method} {address}")
            return httpx.Response(500, json={"error": {"message": "eval: 외부 쓰기 요청은 보내지 않는다"}})
        return self.read(address, request.url.params)

    def read(self, address: str, params: httpx.QueryParams) -> httpx.Response:
        if address.startswith(KEYWORD_URL):
            return httpx.Response(200, json=recorded("kakao_keyword_seongsu_food.json"))
        if address.startswith(FORECAST_URL):
            return httpx.Response(200, json=recorded("open_meteo_forecast.json"))
        if address.startswith(WEBKR_URL):
            body = naver_body(self.web_results) if self.web_results is not None else None
            return httpx.Response(200, json=body or recorded("naver_webkr_seongsu_food.json"))
        if address == EVENTS_URL:
            items = [e for e in self.google_events() if in_range(e, params)]
            return httpx.Response(200, json={"kind": "calendar#events", "items": items})
        if address.startswith(f"{EVENTS_URL}/"):
            return self.one_event(address.removeprefix(f"{EVENTS_URL}/"))
        if address.startswith(MESSAGES_URL):
            return httpx.Response(200, json={"resultSizeEstimate": 0})
        return httpx.Response(404, json={"error": {"message": "eval: 등록하지 않은 주소"}})

    def google_events(self) -> list[dict[str, Any]]:
        return [google_event(event, self.today) for event in self.events]

    def one_event(self, event_id: str) -> httpx.Response:
        for event in self.google_events():
            if event["id"] == event_id:
                return httpx.Response(200, json=event)
        return httpx.Response(404, json={"error": {"code": 404, "message": "Not Found"}})

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handle))
