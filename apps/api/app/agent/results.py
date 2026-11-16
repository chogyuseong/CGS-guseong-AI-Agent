"""ToolResult: 모든 도구가 돌려주는 결과 모양.

- 도구는 예외를 밖으로 던지지 않고 ok=False와 error 문장을 돌려준다(예상 못 한 예외는 tracking.py가 한 곳에서 바꾼다).
- 모델은 ok를 보고 성공·실패를 말한다. 완료 보고는 ok=True일 때만 한다(절대 원칙 5).
- sources, fetched_at은 외부 정보(4단계)의 출처 링크와 조회 시각이다. 내부 도구는 비워 둔다.
"""

from datetime import datetime

from pydantic import BaseModel, Field, JsonValue


class ToolResult(BaseModel):
    ok: bool
    data: JsonValue = None
    error: str | None = None
    sources: list[str] = Field(default_factory=list)
    fetched_at: datetime | None = None

    @classmethod
    def success(cls, data: JsonValue = None) -> "ToolResult":
        return cls(ok=True, data=data)

    @classmethod
    def failure(cls, error: str) -> "ToolResult":
        return cls(ok=False, error=error)
