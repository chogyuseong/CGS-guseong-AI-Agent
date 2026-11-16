"""
카카오 로컬 API. 장소 이름을 좌표로 바꾸고(locate), 장소를 검색한다(search_places, 다음 순서에서 추가).

공식 문서(2026-11-16 확인): https://developers.kakao.com/docs/ko/local/dev-guide
- GET https://dapi.kakao.com/v2/local/search/keyword.json, 헤더 Authorization: KakaoAK {REST API 키}
- 파라미터 query(필수), x/y(경도/위도), radius(0~20000m), size(1~15), sort(accuracy|distance)
- 한도: 키워드 검색 일 100,000건. 초과하면 HTTP 400 + code -10 (tools/http.py가 처리)
"""

from django.conf import settings

from tools import http
from tools.http import ExternalAPIError

KEYWORD_URL = "https://dapi.kakao.com/v2/local/search/keyword.json"
SERVICE = "카카오"


def locate(place):
    """장소 이름 하나를 좌표로 바꾼다. 가장 정확도가 높은 결과 1개를 쓴다.

    돌려주는 값: {name, address, latitude, longitude, place_url}
    """
    documents = keyword_search(place, size=1)
    if not documents:
        raise ExternalAPIError(f"'{place}' 위치를 찾지 못했습니다. 동네나 역 이름으로 다시 말해 주세요.")
    first = documents[0]
    return {
        "name": first.get("place_name", place),
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
