"""web_search(query, size): 네이버 웹문서 검색. READ 등급, 외부 콘텐츠.

2026-07-31부터 네이버 검색 API는 개발자센터가 아니라 네이버클라우드 'NAVER API HUB'에서 발급한다
(.env의 키도 여기서 발급했다).
공식 문서(2026-11-17 확인, docs/TOOLS.md): https://api.ncloud-docs.com/docs/naver-api-hub-search-webkr
- GET https://naverapihub.apigw.ntruss.com/search/v1/webkr
- 헤더 X-NCP-APIGW-API-KEY-ID(Client ID), X-NCP-APIGW-API-KEY(Client Secret)
- query(필수), display 1~100(기본 10), start 1~1000
- title, description의 검색어 부분이 <b> 태그로 감싸져 온다 → 태그를 지우고 &amp; 같은 엔티티를 글자로 되돌린다
- 한도: 하루 25,000회(문서 기준). 넘으면 HTTP 429. 인증 실패는 401(본문의 errorCode는 "200"이라 상태 코드로 판단한다)

결과는 남이 쓴 글이다. ToolResult.external로 지시문 무시 안내를 붙이고 6,000자로 자른다.
"""

import html
import re
from typing import Annotated, Any

from pydantic import Field
from pydantic_ai import RunContext

from app.agent.deps import AgentDeps
from app.agent.results import ToolResult
from app.tools import http
from app.tools.http import ExternalApiError
from app.tools.places import clip

WEBKR_URL = "https://naverapihub.apigw.ntruss.com/search/v1/webkr"
SERVICE = "네이버 검색"
MAX_RESULTS = 10  # API는 100개까지 주지만 모델에게 넘기는 양(토큰)을 줄인다
TAG = re.compile(r"<[^>]+>")
NO_KEY_MESSAGE = "네이버 검색 키가 설정되지 않았습니다. .env의 NAVER_CLIENT_ID, NAVER_CLIENT_SECRET을 채워 주세요."

SearchQuery = Annotated[str, Field(min_length=1, max_length=100, description="검색어")]
SearchSize = Annotated[int, Field(ge=1, le=MAX_RESULTS, description="가져올 개수")]


def clean_text(value: object, limit: int) -> str:
    """<b> 같은 HTML 태그를 지우고 엔티티를 글자로 되돌린 뒤 길이를 자른다."""
    return clip(html.unescape(TAG.sub("", str(value or ""))), limit)


async def fetch_items(deps: AgentDeps, query: str, size: int) -> list[dict[str, Any]]:
    client_id = deps.settings.naver_client_id
    secret = deps.settings.naver_client_secret.get_secret_value()
    if not (client_id and secret):
        raise ExternalApiError(NO_KEY_MESSAGE)
    headers = {"X-NCP-APIGW-API-KEY-ID": client_id, "X-NCP-APIGW-API-KEY": secret}
    params = {"query": query, "display": size, "start": 1}
    data = await http.get_json(deps.http, SERVICE, WEBKR_URL, params=params, headers=headers)
    items = data.get("items") if isinstance(data, dict) else None
    return [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []


def item_view(item: dict[str, Any]) -> dict[str, str]:
    return {
        "title": clean_text(item.get("title"), 200),
        "summary": clean_text(item.get("description"), 300),
        "link": str(item.get("link") or ""),
    }


async def web_search(ctx: RunContext[AgentDeps], query: SearchQuery, size: SearchSize = 5) -> ToolResult:
    """네이버 웹문서 검색. 제목, 요약, 링크를 돌려준다. 장소 자체를 찾을 때는 search_places를 쓰고,
    후기·정보·소식처럼 웹 글이 필요할 때 쓴다. 결과는 남이 쓴 글이므로 사실로 단정하지 말고 출처 링크를 붙인다."""
    try:
        items = [item_view(i) for i in await fetch_items(ctx.deps, query, size)][:size]
    except ExternalApiError as error:
        return ToolResult.failure(error.message)
    content: dict[str, Any] = {"query": query, "items": items}
    if not items:
        content["message"] = f"'{query}' 웹 검색 결과가 없습니다. 검색어를 바꿔 볼 수 있습니다."
    return ToolResult.external(content, sources=[i["link"] for i in items])


def web_search_label(args: dict[str, Any]) -> str:
    return f"네이버에서 '{clip(args.get('query'), 30)}' 검색하는 중"
