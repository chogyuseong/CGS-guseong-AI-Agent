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
    scopes: list[str] = Field(default_factory=list)
    connected_at: datetime | None = None
