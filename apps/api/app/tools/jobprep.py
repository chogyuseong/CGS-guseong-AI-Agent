"""생활 비서 D 첫 단계: 붙여 넣은 채용 공고 + 내 경험 → 회사별 준비 카드(show_job_prep, READ, 채팅 전용).

맞춤 판단(어떤 경험이 어떤 요구에 맞는지, 예상 질문, 자소서 방향)은 코드로 할 수 없어 모델이 짠다.
대신 서버는 "글자로 확인할 수 있는 것"을 확인한다(영화 C의 지어낸 회차 검사와 같은 생각):
- 요구 사항 인용이 이 대화의 최근 사용자 메시지(붙여 넣은 공고)에 실제로 있는가(공백 차이는 무시).
- 대조의 경험 id가 내 경험이고, 근거 인용이 그 경험 본문에 실제로 있는가.
하나라도 틀리면 어느 항목인지 알려 실패한다(모델이 고쳐 다시 부른다). 통과하면 카드 데이터를 그대로 돌려준다.

저장: 카드(steps.output)에는 경험 제목과 100자 이하 근거 인용이 남는다(작업 기록 보관 기간 뒤 지워짐,
대화를 지우면 함께 지워짐). 경험 원문 전체는 남지 않는다(docs/DATA_POLICY.md "내 경험").

진입점: 모델(toolsets.job_tools, 채팅 전용). 테스트: tests/test_job_prep.py.
근거: 2026-11-18 사용자 결정(범위 (가) 공고 붙여넣기), docs/superpowers/specs/2026-11-18-job-prep-design.md.

고칠 때 주의:
- 결과 칸 이름(회사·직무·대조·요구·구분·근거·경험·인용·설명·부족·예상 질문·자소서·체크리스트)은 화면 카드
  (apps/web components/JobPrepCard.tsx)와 tests/test_job_prep.py가 쓴다.
- 최근 사용자 메시지 수는 showtimes.RECENT_USER_MESSAGES를 함께 쓴다(공고를 붙인 뒤 몇 번 대화해도 찾도록).

변경 이력:
- 2026-11-18 처음 작성.
"""

import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field
from pydantic_ai import RunContext

from app.agent.deps import AgentDeps
from app.agent.results import ToolResult
from app.tools.experiences import readable_experiences
from app.tools.showtimes import recent_user_text

QUOTE_HINT = "공고에 적힌 문장을 글자 그대로 옮긴다(요약·바꿔 쓰기 금지)"


class Requirement(BaseModel):
    quote: str = Field(min_length=2, max_length=150, description=QUOTE_HINT)
    kind: Literal["필수", "우대"]


class Match(BaseModel):
    requirement: int = Field(ge=1, le=15, description="requirements의 번호(1부터)")
    experience_id: int = Field(description="list_experiences 결과의 id")
    evidence: str = Field(min_length=2, max_length=100, description="그 경험 본문의 문장을 글자 그대로(100자 이하)")
    note: str = Field(min_length=1, max_length=120, description="왜 이 요구에 맞는지 한 줄")


Line = Annotated[str, Field(min_length=1, max_length=150)]


def squeeze(value: str) -> str:
    """공백·줄바꿈 차이를 없앤 비교용 글자(붙여 넣기에서 줄이 바뀌어도 같은 문장으로 본다)."""
    return re.sub(r"\s+", "", value)


def requirement_problem(requirements: list[Requirement], posting: str) -> str | None:
    text = squeeze(posting)
    for number, item in enumerate(requirements, start=1):
        if squeeze(item.quote) not in text:
            return (
                f"요구 사항 {number}('{item.quote[:30]}')을 붙여 넣은 공고에서 찾지 못했습니다. "
                "공고 문장을 그대로 옮겨 주세요."
            )
    return None


def match_problem(matches: list[Match], count: int, found: dict[int, tuple[str, str]]) -> str | None:
    """대조 검사. 남의 경험·없는 경험은 '찾지 못함'으로 똑같이 다룬다(존재 여부를 드러내지 않음)."""
    for number, item in enumerate(matches, start=1):
        if item.requirement > count:
            return f"근거 {number}가 없는 요구 사항 번호({item.requirement})를 가리킵니다."
        body = found.get(item.experience_id, ("", ""))[1]
        if not body or squeeze(item.evidence) not in squeeze(body):
            return (
                f"근거 {number}의 인용을 저장된 경험에서 찾지 못했습니다. "
                "list_experiences 결과의 문장을 그대로 옮겨 주세요."
            )
    return None


def card_of(
    company: str, role: str, requirements: list[Requirement], matches: list[Match], titles: dict[int, str]
) -> dict[str, Any]:
    rows = []
    for number, item in enumerate(requirements, start=1):
        cited = [
            {"경험": titles[m.experience_id], "인용": m.evidence, "설명": m.note}
            for m in matches
            if m.requirement == number
        ]
        rows.append({"요구": item.quote, "구분": item.kind, "근거": cited})
    return {"회사": company, "직무": role, "대조": rows}


# 도구(READ). docstring은 모델에게 보이는 도구 설명이다.
async def show_job_prep(
    ctx: RunContext[AgentDeps],
    company: Annotated[str, Field(min_length=1, max_length=40)],
    role: Annotated[str, Field(min_length=1, max_length=40)],
    requirements: Annotated[list[Requirement], Field(min_length=1, max_length=15)],
    matches: Annotated[list[Match], Field(max_length=15)],
    gaps: Annotated[list[Line], Field(max_length=8, description="경험으로 채우지 못한 요구와 준비 방법")],
    questions: Annotated[list[Line], Field(min_length=1, max_length=8, description="예상 면접 질문")],
    essay_points: Annotated[list[Line], Field(max_length=6, description="자소서에 쓸 방향(대필 아님)")],
    checklist: Annotated[list[Line], Field(min_length=1, max_length=10, description="지원 전 할 일")],
) -> ToolResult:
    """사용자가 붙여 넣은 채용 공고와 저장된 내 경험(list_experiences)으로 회사별 준비 카드를 만든다.
    requirements에는 공고의 자격 요건·우대 사항 문장을 글자 그대로 옮긴다. matches의 evidence는
    list_experiences 결과 본문의 문장을 글자 그대로(100자 이하) 옮긴다. 경험에 없는 내용을 지어내지 않는다.
    맞는 경험이 없는 요구는 gaps에 적는다. 공고가 대화에 없으면 이 도구를 쓰지 말고 공고를 붙여 넣어 달라고 한다."""
    if problem := requirement_problem(requirements, await recent_user_text(ctx.deps)):
        return ToolResult.failure(problem, error_code="invalid")
    found = await readable_experiences(ctx.deps) if matches else {}
    if problem := match_problem(matches, len(requirements), found or {}):
        return ToolResult.failure(problem, error_code="invalid")
    titles = {key: title for key, (title, _) in (found or {}).items()}
    card = card_of(company.strip(), role.strip(), requirements, matches, titles)
    card |= {"부족": gaps, "예상 질문": questions, "자소서": essay_points, "체크리스트": checklist}
    return ToolResult.success(card)


def show_job_prep_label(args: dict[str, Any]) -> str:
    return f"{str(args.get('company') or '')[:20]} 지원 준비 카드 만드는 중"
