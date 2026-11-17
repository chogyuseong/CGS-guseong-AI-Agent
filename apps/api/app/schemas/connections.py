"""외부 서비스 연결 상태 응답. 토큰은 절대 응답에 넣지 않는다."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class ConnectionOut(BaseModel):
    provider: Literal["google"]
    # 서버 설정(.env의 GOOGLE_*, TOKEN_ENCRYPTION_KEY)이 갖춰져 연결을 시작할 수 있는가. false면 '연결' 버튼을 막는다.
    available: bool
    connected: bool
    # 캘린더 일정 권한이 있는가(세부 동의 화면에서 뺄 수 있다)
    calendar: bool
    # 7단계: 메일 읽기 권한(gmail.readonly)이 있는가.
    # false면 '메일 읽기 권한 추가'(POST /api/connections/google/start {scope: "mail"})를 보여 준다.
    # 메일 권한만 따로 끌 수는 없다(Google은 합친 권한을 한꺼번에 철회한다). 빼려면 연결 해제 후 다시 연결한다.
    mail: bool
    scopes: list[str] = Field(default_factory=list)
    connected_at: datetime | None = None
    # 7단계 보강: 토큰 갱신이 invalid_grant(만료·Google에서 권한 철회)로 실패해 다시 연결해야 한다.
    # 이때 connected·calendar·mail은 false다(처음부터 연결 안 된 것과 이 값으로 구분한다).
    reconnect_required: bool
    # 7단계 보강: 메일 기능 플래그. 서버에 PAYLOAD_ENCRYPTION_KEY가 없으면 false(메일 권한 추가·메일 도구를 쓸 수 없다).
    mail_enabled: bool
    # 7단계 보강: 메일 데이터 처리 동의 여부. 동의 전에는 메일 도구가 Gmail을 부르지 않는다.
    # PUT/DELETE /api/connections/google/mail-consent로 동의·철회한다.
    mail_consent: bool


class GoogleStartRequest(BaseModel):
    """연결 시작(4단계 보강: POST). scope="mail"이면 '메일 읽기 권한 추가'(캘린더 + gmail.readonly)."""

    scope: Literal["mail"] | None = None


class GoogleStartOut(BaseModel):
    """화면은 이 주소로 이동한다(window.location). state는 서버 DB에만 있다."""

    authorization_url: str


class ConnectionErrorDetail(BaseModel):
    # not_configured: 서버에 Google 설정이 없음. mail_disabled(7단계 보강): 메일 기능 플래그가 꺼져 있음.
    code: Literal["not_configured", "mail_disabled"]
    message: str


class ConnectionErrorOut(BaseModel):
    detail: ConnectionErrorDetail
