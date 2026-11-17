"""eval용 가짜 외부 API(httpx.MockTransport). 실제 카카오·네이버·Open-Meteo·Google을 부르지 않는다.

- 조회(GET)는 사례가 정한 세상(EvalInputs)으로 답한다. 정하지 않은 것은 tests/fixtures/의 녹화본을 쓴다.
  - 카카오: size=1 요청(장소 이름 → 좌표, places.locate)은 그 이름 그대로의 지명 하나, 그 밖은 사례의 places나 녹화본.
  - Open-Meteo: 요청한 날짜로 녹화본의 날짜를 바꾸고, 사례가 그 날짜의 날씨를 정했으면 그 값을 넣는다.
  - Google 캘린더·Gmail: 사례의 일정·메일로 만든 응답.
- 쓰기(GET 밖의 모든 요청)는 보내지 않고 기록만 한 뒤 500으로 답한다(승인하지 않으므로 원래 생기면 안 된다).
- 등록하지 않은 주소는 404로 답하고 기록한다(네트워크로 나가지 않는다).
- 받은 요청 전체(쿼리 포함)와 응답에 들어 있던 링크를 모아 둔다(개인정보 전송·출처 평가에 쓴다).
"""

import base64
import copy
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from email.utils import format_datetime
from pathlib import Path
from typing import Any

import httpx

from app.tools.gcal import EVENTS_URL
from app.tools.gmail import MESSAGES_URL
from app.tools.places import KEYWORD_URL
from app.tools.weather import FORECAST_URL, OPEN_METEO_SOURCE
from app.tools.websearch import WEBKR_URL
from evals.cases import CalendarEvent, EvalInputs, MailMessage, PlaceDoc, WeatherDay, WebResult

RECORDED = Path(__file__).resolve().parents[1] / "tests" / "fixtures"
# 사례가 정하지 않았을 때 쓰는 녹화본(데이터셋 버전 해시에도 들어간다, evals/record.py).
RECORDED_FILES = ("kakao_keyword_seongsu_food.json", "naver_webkr_seongsu_food.json", "open_meteo_forecast.json")
URL_PATTERN = re.compile(r"https?://[^\s\"'<>\\]+")
LOCATE_URL = "http://place.map.kakao.com/1"


def recorded(name: str) -> Any:
    return json.loads((RECORDED / name).read_text(encoding="utf-8"))


def google_event(event: CalendarEvent) -> dict[str, Any]:
    day = event.date.isoformat()
    return {
        "kind": "calendar#event",
        "id": event.id,
        "status": "confirmed",
        "etag": f'"{event.id}-1"',
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


def kakao_doc(place: PlaceDoc) -> dict[str, Any]:
    return {
        "place_name": place.name,
        "address_name": place.address,
        "road_address_name": place.address,
        "category_name": place.category,
        "phone": place.phone,
        "place_url": place.url,
        "x": "127.0500",
        "y": "37.5400",
        "distance": "350",
    }


def locate_body(query: str) -> dict[str, Any]:
    """장소 이름 → 좌표 변환 응답: 그 이름 그대로의 지명 하나(좌표 값은 결과에 영향이 없다)."""
    place = PlaceDoc(name=query, address=f"{query} 일대", category="지명", url=LOCATE_URL)
    return {"documents": [kakao_doc(place)], "meta": {"total_count": 1, "is_end": True}}


def forecast_body(day: date, weather: WeatherDay | None) -> dict[str, Any]:
    """녹화본(2026-11-20 하루)의 날짜를 요청한 날로 바꾸고, 사례가 정한 날씨가 있으면 그 값을 넣는다."""
    body = copy.deepcopy(recorded("open_meteo_forecast.json"))
    hourly, daily = body["hourly"], body["daily"]
    hourly["time"] = [f"{day.isoformat()}{t[10:]}" for t in hourly["time"]]
    daily["time"] = [day.isoformat()]
    if weather is not None:
        hours = len(hourly["time"])
        hourly["precipitation_probability"] = [weather.rain_pct] * hours
        hourly["weather_code"] = [weather.code] * hours
        daily.update(
            temperature_2m_min=[weather.min_c],
            temperature_2m_max=[weather.max_c],
            precipitation_probability_max=[weather.rain_pct],
            weather_code=[weather.code],
        )
    return body


def gmail_message(mail: MailMessage) -> dict[str, Any]:
    """Gmail users.messages.get 모양(text/plain 한 부분). metadata 요청에도 같은 것을 준다."""
    data = base64.urlsafe_b64encode(mail.body.encode("utf-8")).decode("ascii")
    headers = {"From": mail.sender, "To": "me@example.com", "Subject": mail.subject}
    headers["Date"] = format_datetime(mail.received)
    return {
        "id": mail.id,
        "threadId": mail.id,
        "labelIds": ["INBOX", "UNREAD"],
        "snippet": " ".join(mail.body.split())[:100],
        "internalDate": str(int(mail.received.timestamp() * 1000)),
        "payload": {
            "partId": "",
            "mimeType": "text/plain",
            "filename": "",
            "headers": [{"name": name, "value": value} for name, value in headers.items()],
            "body": {"size": len(mail.body), "data": data},
        },
    }


@dataclass
class EvalHttp:
    world: EvalInputs
    requests: list[str] = field(default_factory=list)
    writes: list[str] = field(default_factory=list)
    served_urls: list[str] = field(default_factory=list)

    def handle(self, request: httpx.Request) -> httpx.Response:
        address = str(request.url.copy_with(query=None))
        self.requests.append(f"{request.method} {request.url}")
        if request.method != "GET":
            self.writes.append(f"{request.method} {address}")
            return httpx.Response(500, json={"error": {"message": "eval: 외부 쓰기 요청은 보내지 않는다"}})
        response = self.read(address, request.url.params)
        self.collect_urls(response)
        return response

    def collect_urls(self, response: httpx.Response) -> None:
        for url in URL_PATTERN.findall(response.text):
            if url not in self.served_urls:
                self.served_urls.append(url)

    def read(self, address: str, params: httpx.QueryParams) -> httpx.Response:
        if address.startswith(KEYWORD_URL):
            return httpx.Response(200, json=self.kakao(params))
        if address.startswith(FORECAST_URL):
            return self.forecast(params)
        if address.startswith(WEBKR_URL):
            results = self.world.web_results
            return httpx.Response(200, json=naver_body(results) if results else recorded(RECORDED_FILES[1]))
        if address == EVENTS_URL:
            items = [e for e in self.google_events() if in_range(e, params)]
            return httpx.Response(200, json={"kind": "calendar#events", "items": items})
        if address.startswith(f"{EVENTS_URL}/"):
            return self.one_event(address.removeprefix(f"{EVENTS_URL}/"))
        if address.startswith(MESSAGES_URL):
            return self.mail(address.removeprefix(MESSAGES_URL).strip("/"))
        return httpx.Response(404, json={"error": {"message": "eval: 등록하지 않은 주소"}})

    def kakao(self, params: httpx.QueryParams) -> dict[str, Any]:
        if params.get("size") == "1":
            return locate_body(params.get("query", ""))
        if self.world.places is None:
            return recorded(RECORDED_FILES[0])
        docs = [kakao_doc(place) for place in self.world.places]
        return {"documents": docs, "meta": {"total_count": len(docs), "is_end": True}}

    def forecast(self, params: httpx.QueryParams) -> httpx.Response:
        day = date.fromisoformat(params.get("start_date", ""))
        response = httpx.Response(200, json=forecast_body(day, self.world.weather.get(day)))
        self.served_urls.append(OPEN_METEO_SOURCE)  # 날씨 도구가 출처로 붙이는 주소
        return response

    def google_events(self) -> list[dict[str, Any]]:
        return [google_event(event) for event in self.world.calendar_events]

    def one_event(self, event_id: str) -> httpx.Response:
        for event in self.google_events():
            if event["id"] == event_id:
                return httpx.Response(200, json=event)
        return httpx.Response(404, json={"error": {"code": 404, "message": "Not Found"}})

    def mail(self, message_id: str) -> httpx.Response:
        mails = self.world.mail_messages
        if not message_id:
            listed = [{"id": m.id, "threadId": m.id} for m in mails]
            body: dict[str, Any] = {"messages": listed} if listed else {}  # 빈 결과에는 messages 칸이 없다
            return httpx.Response(200, json={**body, "resultSizeEstimate": len(listed)})
        for mail in mails:
            if mail.id == message_id:
                return httpx.Response(200, json=gmail_message(mail))
        return httpx.Response(404, json={"error": {"code": 404, "message": "Not Found"}})

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handle))
