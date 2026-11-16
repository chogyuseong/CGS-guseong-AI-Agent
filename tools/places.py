"""
카카오 로컬 API. 장소 이름을 좌표로 바꾸고(locate), 장소를 검색한다(search_places, READ 등급).

공식 문서(2026-11-16 확인): https://developers.kakao.com/docs/ko/local/dev-guide
- GET https://dapi.kakao.com/v2/local/search/keyword.json, 헤더 Authorization: KakaoAK {REST API 키}
- 파라미터 query(필수), x/y(경도/위도), radius(0~20000m), size(1~15), sort(accuracy|distance)
- 한도: 키워드 검색 일 100,000건. 초과하면 HTTP 400 + code -10 (tools/http.py가 처리)
"""

from django.conf import settings

from agent.models import RiskLevel
from agent.registry import Tool, error_result, ok_result, register
from tools import http
from tools.http import ExternalAPIError

KEYWORD_URL = "https://dapi.kakao.com/v2/local/search/keyword.json"
SERVICE = "카카오"
MAX_RADIUS_M = 20000  # 카카오 radius 최대값
MAX_SIZE = 15  # 카카오 size 최대값


def search_places(user, query, near=None, radius_m=2000, size=5):
    """키워드로 장소를 찾는다. near가 있으면 그 위치를 중심으로 radius_m 안에서 찾는다."""
    try:
        params = {"size": size}
        if near:
            center = locate(near)
            # 카카오는 x가 경도, y가 위도다.
            params.update(x=center["longitude"], y=center["latitude"], radius=radius_m)
        documents = keyword_search(query, **params)
    except ExternalAPIError as error:
        return error_result(str(error))
    if not documents:
        where = f"'{near}' 근처에서 " if near else ""
        return error_result(f"{where}'{query}' 검색 결과가 없습니다. 검색어나 범위를 바꿔 보세요.")
    places = [_to_place(document) for document in documents]
    data = {"query": query, "near": near, "radius_m": radius_m if near else None, "places": places}
    return ok_result(data, sources=[p["place_url"] for p in places if p["place_url"]])


def _to_place(document):
    """LLM에 필요한 필드만 남긴다. 결과는 이후 모든 호출에 따라붙으므로 짧을수록 좋다."""
    distance = document.get("distance")
    return {
        "name": document.get("place_name", ""),
        "address": document.get("road_address_name") or document.get("address_name", ""),
        "category": document.get("category_name", ""),
        "phone": document.get("phone") or None,  # 실제 응답에서 빈 문자열인 가게가 있다
        "place_url": document.get("place_url", ""),
        "latitude": float(document["y"]),
        "longitude": float(document["x"]),
        "distance_m": int(distance) if distance else None,  # 좌표를 보냈을 때만 오며, 문자열이다
    }


def locate(place):
    """장소 이름 하나를 좌표로 바꾼다. 가장 정확도가 높은 결과 1개를 쓴다.

    키워드 검색은 동네 이름에도 그 동네의 가게나 거리 같은 장소를 돌려준다
    (예: '성수동' → '서울숲카페거리'). 좌표는 맞지만 이름이 사용자를 헷갈리게 하므로
    name에는 사용자가 말한 이름을 그대로 두고, 실제로 찾은 장소는 matched_place에 따로 남긴다.

    돌려주는 값: {name, matched_place, address, latitude, longitude, place_url}
    """
    documents = keyword_search(place, size=1)
    if not documents:
        raise ExternalAPIError(f"'{place}' 위치를 찾지 못했습니다. 동네나 역 이름으로 다시 말해 주세요.")
    first = documents[0]
    return {
        "name": place,
        "matched_place": first.get("place_name", ""),
        "address": first.get("road_address_name") or first.get("address_name", ""),
        "latitude": float(first["y"]),  # 카카오는 y가 위도, x가 경도다
        "longitude": float(first["x"]),
        "place_url": first.get("place_url", ""),
    }


def keyword_search(query, **params):
    """카카오 키워드 검색의 documents 목록을 돌려준다."""
    if not settings.KAKAO_REST_API_KEY:
        raise ExternalAPIError(
            "카카오 API 키가 설정되지 않았습니다. .env의 KAKAO_REST_API_KEY를 채워 주세요."
        )
    data = http.get_json(
        SERVICE,
        KEYWORD_URL,
        params={"query": query, **params},
        headers={"Authorization": f"KakaoAK {settings.KAKAO_REST_API_KEY}"},
    )
    documents = data.get("documents") if isinstance(data, dict) else None
    return documents if isinstance(documents, list) else []


register(
    Tool(
        name="search_places",
        description=(
            "카카오맵에서 장소(가게, 식당, 카페, 시설)를 키워드로 찾는다. 이름, 주소, 카테고리, 전화, "
            "카카오맵 링크, 거리를 돌려준다. near에 동네나 역 이름을 주면 그 근처 radius_m 안에서 찾는다. "
            "영업시간과 가격은 결과에 없으니 말하지 않는다."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 50,
                    "description": "예: 카페, 파스타",
                },
                "near": {"type": "string", "minLength": 1, "maxLength": 50, "description": "중심 위치(선택)"},
                "radius_m": {"type": "integer", "minimum": 100, "maximum": MAX_RADIUS_M, "default": 2000},
                "size": {"type": "integer", "minimum": 1, "maximum": MAX_SIZE, "default": 5},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        risk_level=RiskLevel.READ,
        run=search_places,
        label=lambda query="", near=None, **_: f"카카오맵에서 {near + ' ' if near else ''}{query} 찾는 중",
    )
)
