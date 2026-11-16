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
    # false면 '메일 읽기 권한 추가'(GET /api/connections/google/start?scope=mail 링크)를 보여 준다.
    # 메일 권한만 따로 끌 수는 없다(Google은 합친 권한을 한꺼번에 철회한다). 빼려면 연결 해제 후 다시 연결한다.
    mail: bool
    scopes: list[str] = Field(default_factory=list)
    connected_at: datetime | None = None
