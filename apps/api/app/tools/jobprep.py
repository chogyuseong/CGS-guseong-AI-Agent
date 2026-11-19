"""생활 비서 D 첫 단계: 붙여 넣은 채용 공고 + 내 경험 → 회사별 준비 카드(show_job_prep, READ, 채팅 전용).

맞춤 판단(어떤 경험이 어떤 요구에 맞는지, 예상 질문, 자소서 방향)은 코드로 할 수 없어 모델이 짠다.
대신 서버는 "글자로 확인할 수 있는 것"을 확인한다(영화 C의 지어낸 회차 검사와 같은 생각):
- 요구 사항 인용이 이 대화의 최근 사용자 메시지 중 한 공고 안에 실제로 있는가(공백 차이는 무시),
  필수·우대 표시가 공고 머리글과 같은가(2026-11-19).
- 대조의 경험 id가 내 경험이고, 근거 인용이 그 경험 본문에 실제로 있는가.
하나라도 틀리면 어느 항목인지 알려 실패한다(모델이 고쳐 다시 부른다). 통과하면 카드 데이터를 그대로 돌려준다.

저장: 카드(steps.output)에는 경험 제목과 100자 이하 근거 인용이 남는다(작업 기록 보관 기간 뒤 지워짐,
대화를 지우면 함께 지워짐). 경험 원문 전체는 남지 않는다(docs/DATA_POLICY.md "내 경험").

진입점: 모델(toolsets.job_tools, 채팅 전용). 테스트: tests/test_job_prep.py.
근거: 2026-11-18 사용자 결정(범위 (가) 공고 붙여넣기), docs/superpowers/specs/2026-11-18-job-prep-design.md.

고칠 때 주의:
- 결과 칸 이름(회사·직무·대조·요구·구분·근거·경험·인용·설명·부족·예상 질문·자소서·체크리스트)은 화면 카드
  (apps/web components/JobPrepCard.tsx)와 tests/test_job_prep.py가 쓴다.
- 최근 사용자 메시지는 showtimes.recent_user_messages(메시지 단위, RECENT_USER_MESSAGES개)를 함께 쓴다.

변경 이력:
- 2026-11-18 처음 작성.
- 2026-11-19 요구 사항 인용을 최근 메시지를 합친 글이 아니라 한 메시지(한 공고) 안에서 찾는다. 회사 이름이 나온 공고가
  있으면 그 공고여야 하고, 공고 머리글(필수·자격 요건·우대)이 있으면 요건의 필수·우대 표시가 머리글과 같아야 한다.
  예전에는 A사 공고의 요건이 B사 카드에, 우대 요건이 필수로 들어가도 통과했다(tests/test_job_prep.py).
"""

import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field
from pydantic_ai import RunContext

from app.agent.deps import AgentDeps
from app.agent.results import ToolResult
from app.tools.experiences import readable_experiences
from app.tools.showtimes import recent_user_messages

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


# 공고 머리글: 줄 맨 앞(기호·괄호 뒤)에 올 때만 머리글로 본다("경력자 우대" 같은 항목 문장은 머리글이 아니다).
# 우대를 먼저 본다. 바꾸면 tests/test_job_prep.py의 test_kind_* 를 같이 본다.
HEADER = re.compile(r"^[\s\[\(【■●◆▶*#\-·]*(우대|필수|자격\s*요건|지원\s*자격)", re.MULTILINE)


def quote_start(quote: str, text: str) -> int | None:
    """공백·줄바꿈 차이를 무시하고 인용이 시작하는 위치. 없으면 None."""
    pattern = r"\s*".join(re.escape(c) for c in quote if not c.isspace())
    found = re.search(pattern, text)
    return found.start() if found else None


def kind_at(posting: str, start: int) -> str | None:
    """인용 위치가 속한 머리글의 구분(필수·우대). 같은 줄 앞부분, 없으면 위쪽 줄의 머리글. 머리글이 없으면 None."""
    headers = [m for m in HEADER.finditer(posting) if m.start() <= start]
    if not headers:
        return None
    return "우대" if headers[-1].group(1) == "우대" else "필수"


def single_posting(requirements: list[Requirement], company: str, messages: list[str]) -> str | None:
    """요구 사항이 모두 들어 있는 한 메시지(공고). 회사 이름이 나온 공고 메시지가 있으면 그중에서만 고른다."""
    holders = [m for m in messages if all(quote_start(r.quote, m) is not None for r in requirements)]
    named = [m for m in messages if HEADER.search(m) and squeeze(company) in squeeze(m)]
    if named:
        holders = [m for m in holders if m in named]
    return holders[0] if holders else None


def requirement_problem(requirements: list[Requirement], company: str, messages: list[str]) -> str | None:
    """요구 사항 인용 검사. 없는 문장, 서로 다른 공고에서 섞은 문장, 다른 회사 공고의 문장, 머리글과 다른 필수·우대를
    어느 항목인지 알려 거절한다. 머리글이 없는 공고는 필수·우대를 확인하지 않는다(글로 확인할 수 없으므로)."""
    for number, item in enumerate(requirements, start=1):
        if not any(quote_start(item.quote, m) is not None for m in messages):
            return (
                f"요구 사항 {number}('{item.quote[:30]}')을 붙여 넣은 공고에서 찾지 못했습니다. "
                "공고 문장을 그대로 옮겨 주세요."
            )
    posting = single_posting(requirements, company, messages)
    if posting is None:
        if any(all(quote_start(r.quote, m) is not None for r in requirements) for m in messages):
            return f"요구 사항이 '{company}' 공고가 아닌 다른 공고에서 왔습니다. {company} 공고의 문장만 옮겨 주세요."
        return "요구 사항이 서로 다른 공고(메시지)에서 왔습니다. 한 공고의 요건만 옮겨 주세요."
    for number, item in enumerate(requirements, start=1):
        start = quote_start(item.quote, posting)
        kind = kind_at(posting, start) if start is not None else None
        if kind is not None and kind != item.kind:
            return f"요구 사항 {number}('{item.quote[:30]}')은 공고에서 '{kind}' 아래에 있습니다. 구분을 고쳐 주세요."
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
    if problem := requirement_problem(requirements, company, await recent_user_messages(ctx.deps)):
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
