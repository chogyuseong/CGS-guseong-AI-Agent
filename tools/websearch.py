"""
web_search(query, size): 네이버 웹문서 검색. READ 등급, external_content=True.

2026-07-31부터 네이버 검색 API는 개발자센터가 아니라 네이버클라우드 'NAVER API HUB'에서 발급한다.
공식 문서(2026-11-16 확인): https://api.ncloud-docs.com/docs/naver-api-hub-search-webkr
- GET https://naverapihub.apigw.ntruss.com/search/v1/webkr
- 헤더 X-NCP-APIGW-API-KEY-ID(Client ID), X-NCP-APIGW-API-KEY(Client Secret)
- query(필수), display 1~100(기본 10), start 1~1000, format json|xml
- title, description의 검색어 부분이 <b> 태그로 감싸져 온다 → 태그를 지운다
- 한도: 월 775,000건, 키당 50 RPS. 초과하면 429. 현재 한시적 무료.

이용약관 7.3조는 결과를 허용 범위를 넘어 저장(캐시 포함)하는 것을 금지한다.
그래서 필요한 필드만 남기고, 워커가 21일 지난 결과를 기록에서 지운다(agent/worker.py).
"""

import html
import re

from django.conf import settings

from agent.models import RiskLevel
from agent.registry import Tool, error_result, ok_result, register
from tools import http
from tools.http import ExternalAPIError

WEBKR_URL = "https://naverapihub.apigw.ntruss.com/search/v1/webkr"
SERVICE = "네이버 검색"
MAX_RESULTS = 10  # API는 100개까지 주지만 LLM에 넘기는 양(토큰)을 줄인다
TAG = re.compile(r"<[^>]+>")


def clean_text(text):
    """<b> 같은 HTML 태그를 지우고 &amp; 같은 엔티티를 글자로 되돌린다."""
    return html.unescape(TAG.sub("", text or "")).strip()


def web_search(user, query, size=5):
    try:
        items = _fetch(query, size)
    except ExternalAPIError as error:
        return error_result(str(error))
    if not items:
        return error_result(f"'{query}' 웹 검색 결과가 없습니다. 검색어를 바꿔 보세요.")
    results = [
        {
            "title": clean_text(item.get("title")),
            "summary": clean_text(item.get("description")),
            "link": item.get("link", ""),
        }
        for item in items
    ]
    return ok_result({"query": query, "items": results}, sources=[r["link"] for r in results if r["link"]])


def _fetch(query, size):
    if not (settings.NAVER_CLIENT_ID and settings.NAVER_CLIENT_SECRET):
        raise ExternalAPIError(
            "네이버 검색 키가 설정되지 않았습니다. .env의 NAVER_CLIENT_ID, NAVER_CLIENT_SECRET을 채워 주세요."
        )
    data = http.get_json(
        SERVICE,
        WEBKR_URL,
        params={"query": query, "display": size, "start": 1, "format": "json"},
        headers={
            "X-NCP-APIGW-API-KEY-ID": settings.NAVER_CLIENT_ID,
            "X-NCP-APIGW-API-KEY": settings.NAVER_CLIENT_SECRET,
        },
    )
    items = data.get("items") if isinstance(data, dict) else None
    return items if isinstance(items, list) else []


register(
    Tool(
        name="web_search",
        description=(
            "네이버 웹문서 검색. 제목, 요약, 링크를 돌려준다. "
            "장소 자체를 찾을 때는 search_places를 쓰고, 후기·정보·소식처럼 웹 글이 필요할 때 쓴다. "
            "결과는 남이 쓴 글이므로 사실로 단정하지 말고 출처를 붙인다."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 1, "maxLength": 100},
                "size": {"type": "integer", "minimum": 1, "maximum": MAX_RESULTS, "default": 5},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        risk_level=RiskLevel.READ,
        run=web_search,
        label=lambda query="", **_: f"네이버에서 '{query}' 검색 중",
        external_content=True,  # 검색 결과 속 지시문을 명령으로 받지 않도록 경고문으로 감싼다
    )
)
