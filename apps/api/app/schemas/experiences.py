"""내 경험 API 응답·요청(app/routers/experiences.py). 웹은 이 모양을 OpenAPI에서 타입으로 생성한다.

화면 사용처: apps/web/lib/experiences.ts, components/ExperienceList.tsx(설정 화면 '내 경험').
생활 비서 D 첫 단계, 2026-11-18 사용자 결정(원문 암호화 + 등록 전 안내).

고칠 때 주의: 모양을 바꾸면 gen:api 후 lib/experiences.ts를 고치고 tests/test_experiences_api.py의 키 목록을 맞춘다.
길이 제한은 app/tools/experiences.py의 상수를 쓴다(화면 글자 수 표시도 같은 값).
"""

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints

from app.tools.experiences import MAX_BODY_CHARS, MAX_TITLE_CHARS


class ExperienceSummaryOut(BaseModel):
    """목록 한 줄. 본문은 넣지 않는다(목록 조회로 원문이 오가지 않게)."""

    id: int
    title: str
    length: int = Field(description="본문 글자 수")
    created_at: datetime


class ExperienceListOut(BaseModel):
    """enabled=False면 암호화 키가 없어 등록·본문 보기가 막혀 있다(화면이 안내)."""

    experiences: list[ExperienceSummaryOut]
    limit: int
    max_body_chars: int
    enabled: bool


class ExperienceOut(ExperienceSummaryOut):
    """본문까지(하나 보기)."""

    body: str


class ExperienceIn(BaseModel):
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_TITLE_CHARS)]
    body: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_BODY_CHARS)]
