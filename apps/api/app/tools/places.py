"""카카오 로컬 API: 장소 검색(search_places, READ)과 장소 이름 → 좌표 변환(locate, get_weather가 쓴다).

공식 문서(2026-11-17 확인, docs/TOOLS.md): https://developers.kakao.com/docs/ko/local/dev-guide
- GET https://dapi.kakao.com/v2/local/search/keyword.json, 헤더 Authorization: KakaoAK {REST API 키}
- query(필수), x/y(경도/위도), radius(0~20000m), size(1~15), sort(accuracy|distance)
- 응답: documents[{place_name, category_name, phone, address_name, road_address_name, x, y, place_url, distance}]
- 한도: 키워드 검색 하루 100,000건. 넘으면 HTTP 400 + code -10(app/tools/http.py가 "호출 한도"로 바꾼다).

결과는 외부 콘텐츠다(가게 이름·주소는 남이 쓴 글). ToolResult.external로 지시문 무시 안내를 붙인다.
결과가 없으면 실패가 아니라 "없음"이라는 정상 답이다(ok=true, places=[], message).

진입점: 모델(toolsets.read_tools의 search_places, 예약·감시 task 포함), weather.get_weather(locate로 좌표 변환).
다른 도구 파일이 clip()을 가져다 쓴다(weather, websearch, drafts, gcal, gmail).
근거: docs/TOOLS.md "search_places", docs/DECISIONS.md 4단계(2026-11-17 외부 호출 공통, 감싸는 도구에 장소 포함).
데이터 정책: public만 받는다(data_policy.TOOL_POLICIES). 같은 작업에서 메일을 읽었거나 조회한 일정 제목이 인자에 있으면
StepTracking이 실행 전에 막고, 메일을 읽은 문맥에서는 모델에게 보이지도 않는다(data_policy.visible_tool).

고칠 때 주의:
- 등록부 네 곳: toolsets.read_tools와 TOOL_LABELS(search_places_label), data_policy.TOOL_POLICIES,
  results.EXTERNAL_CONTENT_TOOLS(외부 글 감싸기 확인·remember 검사).
- KEYWORD_URL은 evals/fake_externals.py·evals/evaluators.py가 가져다 가짜 응답과
  개인정보 전송 판정(PUBLIC_HOSTS)에 쓴다.
- 응답 칸 이름을 바꾸면 tests/fixtures/kakao_*.json(README.md에 실제 응답인지 적혀 있다)과 tests/test_places.py,
  docs/TOOLS.md를 같이 고친다. 키·한도 오류 해석은 app/tools/http.py에 있다.
"""

from dataclasses import dataclass
from typing import Annotated, Any

from pydantic import Field
from pydantic_ai import RunContext

from app.agent.deps import AgentDeps
from app.agent.results import ToolResult
from app.tools import http
from app.tools.http import ExternalApiError

KEYWORD_URL = "https://dapi.kakao.com/v2/local/search/keyword.json"
SERVICE = "카카오"
MAX_RADIUS_M = 20000  # 카카오 radius 최대값
MAX_SIZE = 15  # 카카오 size 최대값
# 이름·주소·카테고리 한 칸의 최대 글자 수. 외부 글 한 칸이 결과 전체(6,000자 제한)를 차지하지 못하게 한다.
TEXT_LIMIT = 200
NO_KEY_MESSAGE = "카카오 API 키가 설정되지 않았습니다. .env의 KAKAO_REST_API_KEY를 채워 주세요."

PlaceQuery = Annotated[str, Field(min_length=1, max_length=50, description="찾을 것. 예: 파스타, 카페, 약국")]
NearPlace = Annotated[
    str | None, Field(min_length=1, max_length=50, description="중심 위치(동네, 역 이름). 없으면 전국")
]
Radius = Annotated[int, Field(ge=100, le=MAX_RADIUS_M, description="near로부터의 반경(미터)")]
ResultSize = Annotated[int, Field(ge=1, le=MAX_SIZE, description="가져올 개수")]


@dataclass(frozen=True)
class Location:
    """장소 이름 하나를 좌표로 바꾼 결과."""

    name: str  # 사용자가 말한 이름 그대로
    matched_place: str  # 좌표를 잡은 실제 장소(예: '성수동' → '서울숲카페거리')
    address: str
    latitude: float
    longitude: float
    place_url: str


def clip(value: object, limit: int = TEXT_LIMIT) -> str:
    """None·빈 값은 ""로, 앞뒤 공백을 지우고 limit자를 넘으면 끝을 '…'로 자른다.

    외부 문자열을 결과·타임라인 문구에 넣을 때 쓴다.
    """
    text = str(value or "").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


async def keyword_search(deps: AgentDeps, query: str, **params: Any) -> list[dict[str, Any]]:
    """카카오 키워드 검색의 documents 목록을 돌려준다.

    키가 비었으면 요청을 보내지 않고 ExternalApiError(NO_KEY_MESSAGE). HTTP 실패·한도 초과도 http.get_json이
    ExternalApiError로 바꾼다. 응답 모양이 예상과 다르면 예외 대신 빈 목록이다(결과 없음과 같게 처리).
    외부 호출 1회(GET, 재시도 규칙은 http.py). 키는 헤더에만 넣는다(주소·오류 문장에 남지 않게).
    """
    key = deps.settings.kakao_rest_api_key.get_secret_value()
    if not key:
        raise ExternalApiError(NO_KEY_MESSAGE)
    data = await http.get_json(
        deps.http, SERVICE, KEYWORD_URL, params={"query": query, **params}, headers={"Authorization": f"KakaoAK {key}"}
    )
    documents = data.get("documents") if isinstance(data, dict) else None
    return [d for d in documents if isinstance(d, dict)] if isinstance(documents, list) else []


async def locate(deps: AgentDeps, place: str) -> Location:
    """장소 이름을 좌표로 바꾼다. 정확도가 가장 높은 결과 1개를 쓴다. 못 찾으면 ExternalApiError.

    Open-Meteo 지오코딩이 동네·역 이름에 약해 명세대로 카카오를 쓴다(weather.py 머리 주석).
    첫 결과가 다른 곳일 수 있어 matched_place를 남겨 사용자가 어느 장소 기준인지 확인하게 한다. 외부 호출 1회.
    """
    documents = await keyword_search(deps, place, size=1)
    if not documents:
        raise ExternalApiError(f"'{place}' 위치를 찾지 못했습니다. 동네나 역 이름으로 다시 말해 주세요.")
    first = documents[0]
    return Location(
        name=place,
        matched_place=clip(first.get("place_name")),
        address=clip(first.get("road_address_name") or first.get("address_name")),
        latitude=float(first["y"]),  # 카카오는 y가 위도, x가 경도다
        longitude=float(first["x"]),
        place_url=str(first.get("place_url") or ""),
    )


def place_view(document: dict[str, Any]) -> dict[str, Any]:
    """모델과 화면에 필요한 칸만 남긴다(이후 모든 모델 요청에 따라붙는 토큰을 줄인다)."""
    distance = document.get("distance")
    return {
        "name": clip(document.get("place_name")),
        "address": clip(document.get("road_address_name") or document.get("address_name")),
        "category": clip(document.get("category_name")),
        "phone": clip(document.get("phone"), 30) or None,  # 실제 응답에 빈 문자열인 가게가 있다
        "place_url": str(document.get("place_url") or ""),
        "latitude": float(document["y"]),
        "longitude": float(document["x"]),
        "distance_m": int(distance) if distance else None,  # 좌표를 보냈을 때만 오며, 문자열이다
    }


# 도구(READ). docstring은 모델에게 보이는 도구 설명이라 고치지 않는다.
# near가 있으면 외부 호출이 두 번이다(locate로 중심 좌표 → 반경 검색). 오류는 예외로 던지지 않고 ToolResult로 바꾼다.
# 결과는 ToolResult.external로 감싼다: 가게 이름·주소에 숨은 지시문을 따르지 않게(절대 원칙 14).
async def search_places(
    ctx: RunContext[AgentDeps], query: PlaceQuery, near: NearPlace = None, radius_m: Radius = 2000, size: ResultSize = 5
) -> ToolResult:
    """카카오맵에서 장소(식당, 카페, 가게, 시설)를 키워드로 찾는다.
    이름, 주소, 카테고리, 전화, 카카오맵 링크, 거리를 돌려준다.
    near에 동네나 역 이름을 주면 그 근처 radius_m 안에서 찾는다. 영업시간·가격·평점은 결과에 없으니 지어내지 않는다."""
    try:
        params: dict[str, Any] = {"size": size}
        if near:
            center = await locate(ctx.deps, near)
            params.update(x=center.longitude, y=center.latitude, radius=radius_m)
        documents = await keyword_search(ctx.deps, query, **params)
    except ExternalApiError as error:
        return ToolResult.from_api_error(error)
    places = [place_view(d) for d in documents[:size]]
    content: dict[str, Any] = {"query": query, "near": near, "radius_m": radius_m if near else None, "places": places}
    if not places:
        where = f"'{near}' 근처에서 " if near else ""
        content["message"] = f"{where}'{query}' 검색 결과가 없습니다. 검색어나 범위를 바꿔 볼 수 있습니다."
    return ToolResult.external(content, sources=[p["place_url"] for p in places])


def search_places_label(args: dict[str, Any]) -> str:
    """타임라인 문구(toolsets.TOOL_LABELS에 이름으로 등록). 인자 값은 20자로 자른다."""
    near = args.get("near")
    return f"카카오맵에서 {clip(near, 20) + ' ' if near else ''}{clip(args.get('query'), 20)} 찾는 중"
