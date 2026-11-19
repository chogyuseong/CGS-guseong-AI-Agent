"""외부 API 공통 호출. 모든 외부 도구는 이 모듈로만 HTTP를 보낸다.

- 타임아웃 10초. 조회(GET)만 한 번 다시 시도한다(타임아웃, 연결 실패, 5xx). 4xx는 다시 보내도 같아서 하지 않는다.
- 쓰기(POST, PATCH, DELETE)는 다시 시도하지 않는다. 타임아웃이 나도 상대 서버에는 이미 반영됐을 수 있어서
  다시 보내면 같은 일정이 두 번 생길 수 있다(3단계의 "외부 행동은 많아야 한 번"과 같은 원칙).
  그래서 쓰기의 타임아웃·연결 끊김은 "실제로 반영됐는지 확인해 달라"는 문장으로 알린다.
- 실패는 ExternalApiError(사용자에게 보여 줘도 되는 한국어 문장)로 바꾼다. 도구는 이것을 받아
  ToolResult.from_api_error로 돌려준다(1단계 보강 뒤: outcome_unknown이면 unknown, 아니면 failed,
  code가 results.RETRYABLE_CODES(timeout·network·server_error·rate_limited)면 retryable).
- 오류 문장과 로그에는 응답 본문, 요청 주소(쿼리), 헤더(키, 토큰)를 넣지 않는다. 키와 토큰이 모델·화면·로그에 새지 않게.
- 한도 초과는 서비스마다 모양이 다르다: 보통 429, 카카오는 400 + code -10, Google은 403 + rateLimitExceeded 등.

위험 등급: 없음(도구가 아니라 공통 호출 모듈). 쓰기 요청의 위험은 부르는 EXTERNAL 도구가 승인·operation으로 다룬다.
진입점:
- build_http_client: app/main.py create_app이 한 번 만들어 app.state.http_client에 두고(lifespan 끝에서 닫음),
  AgentDeps.http로 도구에 간다(테스트는 tests/fake_http.py의 MockTransport 클라이언트로 바꿔 낀다).
- get_json: app/tools/weather.py, places.py, websearch.py, gmail.py.
- request_json: app/tools/gcal.py(쓰기 포함), app/google_auth.py(토큰 교환·갱신·철회 POST).
- ExternalApiError: 위 모듈들과 app/agent/results.py(from_api_error), google_auth의 연결 오류가 상속한다.
근거: BUILD_SPEC 17절(타임아웃·재시도), 3단계 at-most-once, docs/TOOLS.md "공통 호출 규칙" 표.

변경 이력:
- 2026-11-17 4단계: 공통 호출, GET만 재시도, 쓰기 시간 초과 문장, 한도 판정.
- 1단계 보강: ExternalApiError.code(오류 종류).
- 2026-11-17 7단계: dailyLimitExceeded를 한도에 더함. 7단계 보강: reason(Google errors[].reason) 칸.

고칠 때 주의:
- 재시도·타임아웃 규칙을 바꾸면 docs/TOOLS.md 표와 data_policy.TOOL_POLICIES의 타임아웃·재시도 설명을 같이 고친다.
- 쓰기 재시도를 켜지 않는다(일정이 두 번 생길 수 있다).
  401 재전송은 여기서 하지 않고 각 도구(gcal·gmail)가 토큰을 갱신한 뒤 한 번만 한다.
- 오류 문장의 낱말에 기대는 코드가 있다: gcal.translate·gmail.translate는 한도 문장의 "한도"로 한도 403을 구별한다.
- 테스트: tests/test_tool_result.py, test_places.py, test_websearch.py, test_gcal.py
  (녹화 응답 tests/fixtures/*_error_*.json, 목록은 tests/fixtures/README.md).
"""

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# docs/TOOLS.md "공통 호출 규칙": 모델 요청 한 번보다 오래 기다리지 않는다. 오류 문장("10초")에도 이 값이 들어간다.
TIMEOUT_SECONDS = 10.0
READ_ATTEMPTS = 2  # 처음 1번 + 다시 시도 1번 (GET만)
KAKAO_QUOTA_CODE = -10  # 카카오는 한도 초과를 400 + 본문 code -10으로 알린다(docs/TOOLS.md)
# dailyLimitExceeded는 7단계에 더했다(Gmail 오류 문서: 프로젝트 하루 한도).
GOOGLE_RATE_REASONS = frozenset({"rateLimitExceeded", "userRateLimitExceeded", "quotaExceeded", "dailyLimitExceeded"})


class ExternalApiError(Exception):
    """외부 API 실패. 메시지는 사용자에게 그대로 보여 줘도 되는 한국어 문장이다.

    status: 응답을 받았을 때의 HTTP 상태 코드(도구가 401·404를 따로 처리하는 데 쓴다).
    code: 오류 종류(1단계 보강). timeout, network, server_error, rate_limited, unauthorized, rejected, bad_response.
    outcome_unknown: 쓰기 요청(GET 외)이 응답 없이 끝났다(시간 초과, 연결 끊김, 5xx). 상대 서버에 반영됐을 수 있어
        실패가 아니라 "결과 불명"으로 다룬다(ToolResult.unknown).
    reason: Google 오류의 첫 사유 코드(영어 식별자). gmail.translate가 범위 부족을 가려낼 때 쓴다.

    도구가 직접 만들기도 한다(입력 검사 실패 등, status 없음 → failed). 그래서 status가 None일 수 있다.
    고칠 때 주의: code 값은 results.RETRYABLE_CODES·화면 문구와 맞물린다. 새 code를 만들면 그쪽을 확인한다.
    """

    def __init__(
        self,
        message: str,
        status: int | None = None,
        *,
        code: str = "rejected",
        outcome_unknown: bool = False,
        reason: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code
        self.outcome_unknown = outcome_unknown
        # 7단계 보강: Google 공통 오류의 첫 사유(errors[].reason, 예: insufficientPermissions). 정해진 영어 코드만.
        self.reason = reason


def build_http_client() -> httpx.AsyncClient:
    """앱이 시작할 때 한 번 만든다. 리디렉션은 따라가지 않는다(키가 든 요청이 다른 주소로 가지 않게)."""
    return httpx.AsyncClient(timeout=httpx.Timeout(TIMEOUT_SECONDS), follow_redirects=False)


async def get_json(
    http: httpx.AsyncClient, service: str, url: str, *, params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> Any:  # fmt: skip
    """GET 요청(request_json의 조회 규칙: 시간 초과·연결 실패·5xx면 한 번 다시)."""
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
    retry_read: bool = True,
    read_only: bool = False,
) -> Any:
    """요청을 보내고 JSON을 돌려준다. 본문이 없는 성공 응답(예: 삭제 204)은 {}.

    실패: 응답을 받은 4xx·해석 실패는 read_response가 던진다(재시도 없음, outcome_unknown 아님 → failed).
    응답을 못 받았거나 5xx가 끝까지 이어지면 ExternalApiError(code timeout·network·server_error)이고,
    GET이 아니면 outcome_unknown=True(→ unknown, 도구 규격의 reconcile이 재조회).
    read_only=True는 본문을 POST로 보내지만 상대에 아무것도 바꾸지 않는 조회다(2026-11-18 TMAP 경로 조회).
    그런 요청은 응답이 없어도 '결과 불명'이 아니라 실패(failed)다. 한도를 아끼려고 재시도하지 않는다(1회).
    로그에는 서비스 이름·메서드·code만 남긴다(주소·쿼리·본문·헤더 없음).
    """
    attempts = READ_ATTEMPTS if method == "GET" and retry_read else 1
    kind = "GET" if read_only else method  # 문구는 조회용(다시 보내지 말라는 쓰기용 안내를 붙이지 않는다)
    message, code = "", "network"
    for _ in range(attempts):
        try:
            response = await http.request(
                method, url, params=params, headers=headers, json=json, data=data, timeout=TIMEOUT_SECONDS
            )
        except httpx.TimeoutException:
            message, code = transport_message(service, kind, timed_out=True), "timeout"
            continue
        except httpx.HTTPError:
            message, code = transport_message(service, kind, timed_out=False), "network"
            continue
        if response.status_code >= 500:
            message = f"{service} 서버에 일시적인 문제가 있습니다(상태 {response.status_code})."
            code = "server_error"
            continue
        return read_response(service, response)
    logger.warning("외부 API 실패: service=%s method=%s reason=%s", service, method, code)
    raise ExternalApiError(message, code=code, outcome_unknown=method != "GET" and not read_only)


def transport_message(service: str, method: str, *, timed_out: bool) -> str:
    """응답을 못 받았을 때의 사용자 문장. 쓰기면 "다시 보내지 마세요"를 붙인다(모델·사용자의 중복 재요청 방지)."""
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
    """5xx가 아닌 응답을 해석한다. 순서가 중요하다: 한도 판정을 401·403보다 먼저 해야 Google 한도 403이
    "인증 실패"가 되지 않는다. 오류 문장에는 상태 코드만 넣고 응답 본문은 넣지 않는다(키·남의 글이 새지 않게).
    고칠 때 주의: 한도 문장의 "한도" 낱말은 gcal·gmail의 translate가 찾는다.
    """
    status = response.status_code
    if status == 429 or is_rate_limited(response):
        raise ExternalApiError(
            f"{service} 호출 한도를 넘었습니다. 잠시 후 다시 시도해 주세요.", status, code="rate_limited"
        )
    if status in (401, 403):
        raise ExternalApiError(
            f"{service} 인증에 실패했습니다. API 키와 사용 설정을 확인해 주세요.",
            status,
            code="unauthorized",
            reason=google_reason(response),
        )
    if status >= 400:
        logger.warning("외부 API 요청 거부: service=%s status=%s", service, status)
        raise ExternalApiError(f"{service} 요청이 거부되었습니다(상태 {status}).", status)
    if not response.content:
        return {}
    try:
        return response.json()
    except ValueError as error:
        raise ExternalApiError(f"{service} 응답을 해석하지 못했습니다.", status, code="bad_response") from error


def is_rate_limited(response: httpx.Response) -> bool:
    """429가 아닌 모양의 한도 초과: 카카오 400 + code -10, Google 403 + rateLimitExceeded 계열."""
    if response.status_code not in (400, 403):
        return False
    body = safe_json(response)
    if response.status_code == 400:
        return body.get("code") == KAKAO_QUOTA_CODE
    return bool(set(google_reasons(body)) & GOOGLE_RATE_REASONS)


def google_reasons(body: dict[str, Any]) -> list[str]:
    """Google 공통 오류 본문의 errors[].reason 목록."""
    error = body.get("error")
    errors = error.get("errors") if isinstance(error, dict) else None
    return [e["reason"] for e in errors or [] if isinstance(e, dict) and isinstance(e.get("reason"), str)]


def google_reason(response: httpx.Response) -> str | None:
    """오류 사유 코드 하나(영어 식별자, 80자 이하만). 응답 본문의 다른 글은 꺼내지 않는다.

    ASCII·길이 제한은 사유 칸에 엉뚱한 긴 글이 와도 오류 객체(로그·결과로 갈 수 있음)에 담기지 않게 한다.
    """
    reasons = google_reasons(safe_json(response))
    first = reasons[0] if reasons else None
    return first if first and len(first) <= 80 and first.isascii() else None


def safe_json(response: httpx.Response) -> dict[str, Any]:
    """오류 판정용 본문 읽기. JSON이 아니거나 객체가 아니면 {}(판정만 하고 예외를 내지 않는다)."""
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}
