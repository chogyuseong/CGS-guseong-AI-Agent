"""
외부 API 공통 호출. 모든 외부 도구는 이 함수로만 HTTP를 보낸다.

- 타임아웃 10초, 재시도 1회(타임아웃, 연결 실패, 5xx만). 4xx는 다시 보내도 같은 결과라 재시도하지 않는다.
- 실패는 ExternalAPIError(한국어 문장)로 바꾼다. 도구는 이것을 받아 ok=false로 돌려준다.
- 오류 문장에는 응답 본문, 요청 헤더(키)를 넣지 않는다. LLM과 화면에 키가 새지 않게.
"""

import logging

import httpx

logger = logging.getLogger(__name__)

TIMEOUT_SECONDS = 10
ATTEMPTS = 2  # 처음 1번 + 재시도 1번
KAKAO_QUOTA_EXCEEDED = -10  # 카카오는 한도 초과를 HTTP 400 + code -10으로 알린다


class ExternalAPIError(Exception):
    """외부 API 실패. 메시지는 사용자에게 그대로 보여 줘도 되는 한국어 문장이다."""


def make_client():
    """테스트에서 가짜 전송(MockTransport)으로 바꿔 끼우는 지점."""
    return httpx.Client(timeout=TIMEOUT_SECONDS)


def get_json(service, url, params=None, headers=None):
    """GET 요청 후 JSON을 돌려준다. service는 오류 문장에 쓸 서비스 이름(예: '카카오')."""
    error = None
    with make_client() as client:
        for _ in range(ATTEMPTS):
            try:
                response = client.get(url, params=params, headers=headers)
            except httpx.TimeoutException:
                error = f"{service} 응답이 {TIMEOUT_SECONDS}초 안에 오지 않았습니다."
                continue
            except httpx.HTTPError:
                error = f"{service}에 연결하지 못했습니다."
                continue
            if response.status_code >= 500:
                error = f"{service} 서버에 일시적인 문제가 있습니다(상태 {response.status_code})."
                continue
            return _read(service, response)
    logger.warning("외부 API 실패(%s): %s", service, error)
    raise ExternalAPIError(error)


def _read(service, response):
    status = response.status_code
    if status in (401, 403):
        raise ExternalAPIError(f"{service} 인증에 실패했습니다. API 키와 사용 설정을 확인해 주세요.")
    if status == 429 or _is_kakao_quota(response):
        raise ExternalAPIError(f"{service} 호출 한도를 넘었습니다. 잠시 후 다시 시도해 주세요.")
    if status >= 400:
        logger.warning("외부 API 요청 오류(%s): 상태 %s", service, status)
        raise ExternalAPIError(f"{service} 요청이 거부되었습니다(상태 {status}).")
    try:
        return response.json()
    except ValueError as exc:
        raise ExternalAPIError(f"{service} 응답을 해석하지 못했습니다.") from exc


def _is_kakao_quota(response):
    if response.status_code != 400:
        return False
    try:
        return response.json().get("code") == KAKAO_QUOTA_EXCEEDED
    except (ValueError, AttributeError):
        return False
