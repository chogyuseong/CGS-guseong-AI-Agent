"""외부 API 공통 호출. 모든 외부 도구는 이 모듈로만 HTTP를 보낸다.

- 타임아웃 10초. 조회(GET)만 한 번 다시 시도한다(타임아웃, 연결 실패, 5xx). 4xx는 다시 보내도 같아서 하지 않는다.
- 쓰기(POST, PATCH, DELETE)는 다시 시도하지 않는다. 타임아웃이 나도 상대 서버에는 이미 반영됐을 수 있어서
  다시 보내면 같은 일정이 두 번 생길 수 있다(3단계의 "외부 행동은 많아야 한 번"과 같은 원칙).
  그래서 쓰기의 타임아웃·연결 끊김은 "실제로 반영됐는지 확인해 달라"는 문장으로 알린다.
- 실패는 ExternalApiError(사용자에게 보여 줘도 되는 한국어 문장)로 바꾼다. 도구는 이것을 받아 ok=false로 돌려준다.
- 오류 문장과 로그에는 응답 본문, 요청 주소(쿼리), 헤더(키, 토큰)를 넣지 않는다. 키와 토큰이 모델·화면·로그에 새지 않게.
- 한도 초과는 서비스마다 모양이 다르다: 보통 429, 카카오는 400 + code -10, Google은 403 + rateLimitExceeded 등.
"""

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

TIMEOUT_SECONDS = 10.0
READ_ATTEMPTS = 2  # 처음 1번 + 다시 시도 1번 (GET만)
KAKAO_QUOTA_CODE = -10
# dailyLimitExceeded는 7단계에 더했다(Gmail 오류 문서: 프로젝트 하루 한도).
GOOGLE_RATE_REASONS = frozenset({"rateLimitExceeded", "userRateLimitExceeded", "quotaExceeded", "dailyLimitExceeded"})


class ExternalApiError(Exception):
    """외부 API 실패. 메시지는 사용자에게 그대로 보여 줘도 되는 한국어 문장이다.

    status: 응답을 받았을 때의 HTTP 상태 코드(도구가 401·404를 따로 처리하는 데 쓴다).
    """

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


def build_http_client() -> httpx.AsyncClient:
    """앱이 시작할 때 한 번 만든다. 리디렉션은 따라가지 않는다(키가 든 요청이 다른 주소로 가지 않게)."""
    return httpx.AsyncClient(timeout=httpx.Timeout(TIMEOUT_SECONDS), follow_redirects=False)


async def get_json(
    http: httpx.AsyncClient, service: str, url: str, *, params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> Any:  # fmt: skip
    return await request_json(http, service, "GET", url, params=params, headers=headers)


async def request_json(
    http: httpx.AsyncClient,
    service: str,
    method: str,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    json: Any = None,
    data: dict[str, str] | None = None,
) -> Any:
    """요청을 보내고 JSON을 돌려준다. 본문이 없는 성공 응답(예: 삭제 204)은 {}."""
    attempts = READ_ATTEMPTS if method == "GET" else 1
    message = ""
    for _ in range(attempts):
        try:
            response = await http.request(
                method, url, params=params, headers=headers, json=json, data=data, timeout=TIMEOUT_SECONDS
            )
        except httpx.TimeoutException:
            message = transport_message(service, method, timed_out=True)
            continue
        except httpx.HTTPError:
            message = transport_message(service, method, timed_out=False)
            continue
        if response.status_code >= 500:
            message = f"{service} 서버에 일시적인 문제가 있습니다(상태 {response.status_code})."
            continue
        return read_response(service, response)
    logger.warning("외부 API 실패: service=%s method=%s reason=%s", service, method, message)
    raise ExternalApiError(message)


def transport_message(service: str, method: str, *, timed_out: bool) -> str:
    base = (
        f"{service} 응답이 {int(TIMEOUT_SECONDS)}초 안에 오지 않았습니다."
        if timed_out
        else f"{service}에 연결하지 못했습니다."
    )
    if method == "GET":
        return base
    # 쓰기 요청은 상대 서버에 이미 반영됐을 수 있다. 다시 시도하지 않고 확인을 부탁한다.
    return f"{base} 실제로 반영되었는지 확인할 수 없으니 직접 확인해 주세요. 같은 요청을 다시 보내지 마세요."


def read_response(service: str, response: httpx.Response) -> Any:
    status = response.status_code
    if status == 429 or is_rate_limited(response):
        raise ExternalApiError(f"{service} 호출 한도를 넘었습니다. 잠시 후 다시 시도해 주세요.", status)
    if status in (401, 403):
        raise ExternalApiError(f"{service} 인증에 실패했습니다. API 키와 사용 설정을 확인해 주세요.", status)
    if status >= 400:
        logger.warning("외부 API 요청 거부: service=%s status=%s", service, status)
        raise ExternalApiError(f"{service} 요청이 거부되었습니다(상태 {status}).", status)
    if not response.content:
        return {}
    try:
        return response.json()
    except ValueError as error:
        raise ExternalApiError(f"{service} 응답을 해석하지 못했습니다.") from error


def is_rate_limited(response: httpx.Response) -> bool:
    """429가 아닌 모양의 한도 초과: 카카오 400 + code -10, Google 403 + rateLimitExceeded 계열."""
    if response.status_code not in (400, 403):
        return False
    body = safe_json(response)
    if response.status_code == 400:
        return body.get("code") == KAKAO_QUOTA_CODE
    errors = (body.get("error") or {}).get("errors") if isinstance(body.get("error"), dict) else None
    reasons = {e.get("reason") for e in errors or [] if isinstance(e, dict)}
    return bool(reasons & GOOGLE_RATE_REASONS)


def safe_json(response: httpx.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}
