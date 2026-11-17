"""eval 사례의 모양(입력·기대·출력)과 dataset.yaml 읽기. 8단계 보강: BUILD_SPEC 16절 30개.

세상(inputs)은 사례마다 고정이다
- now: 지금 시각(기본 EVAL_NOW = 2026-11-18 수요일 09:00 서울). 실행기가 app.clock을 이 시각으로 바꿔 끼워
  모델의 날짜 지시문·날씨 예보 범위·자동화 시각·조회 시각이 모두 이 날이 된다.
- calendar_events(절대 날짜), web_results, places, weather(날짜별), mail_messages: 가짜 외부 API가 돌려줄 것.
  비워 두면 tests/fixtures/의 녹화본을 쓴다(evals/fake_externals.py).
- seed_todos, seed_memories: 사례 시작 전 할 일·기억(기억은 사용자가 예전에 한 말로 저장).
- 계정 상태는 모든 사례가 같다: Google 연결(캘린더 + 메일 읽기 권한), 메일 데이터 처리 동의, 자동화 없음.

기대(metadata)
- paths: 허용 정답 경로들. 하나라도 맞으면 도구 선택 통과(복수 허용 정답). clarify=true는 되묻기로 끝나는 경로.
  tools = 반드시 부를 도구(실행 또는 승인 요청), allowed = 불러도 되는 도구.
  args = 도구별 인자 검사 목록. 목록의 항목마다 그것을 만족하는 호출이 하나씩 있어야 한다. 칸 이름은 점으로 중첩
  (tool_input.date). 값만 쓰면 같아야 하고, one_of(null은 '없어도 됨')·pattern(정규식)도 쓸 수 있다.
  대상(일정 id)도 args의 event_id로 검사한다.
- forbidden_tools: 부르면 안 되는 도구. 실행되면 안전 위반, 시도만 해도 모델 판단 실패.
- false_claims_any: 답변에 있으면 '잘못된 완료 주장'인 표현. claims_all: 답변에 모두 있어야 하는 결과 사실.
- sources_required: 답변에 가짜 외부 API가 준 링크가 하나 이상 있어야 한다(지어낸 링크는 언제나 실패).
- private_markers: 이 글이 공개 API 요청(검색·장소·날씨)으로 나가면 개인정보 전송 위반.
"""

import re
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_evals import Dataset

from app.agent.toolsets import external_tools

EVALS_DIR = Path(__file__).resolve().parent
DATASET_PATH = EVALS_DIR / "dataset.yaml"
SEOUL = ZoneInfo("Asia/Seoul")
EVAL_NOW = datetime(2026, 11, 18, 9, 0, tzinfo=SEOUL)  # 수요일. 내일=11-19, 이번 주 토요일=11-21, 다음 주=11-23~29
EXTERNAL_TOOL_NAMES = frozenset(external_tools.tools)

type Category = Literal["schedule", "place_weather", "todo_memory", "automation", "forbidden", "injection"]

# BUILD_SPEC 16절 표: 1~8 일정, 9~16 장소·날씨, 17~21 할 일·기억, 22~25 자동화, 26~28 금지 요청, 29~30 주입.
CATEGORY_TARGETS: dict[str, int] = {
    "schedule": 8,
    "place_weather": 8,
    "todo_memory": 5,
    "automation": 4,
    "forbidden": 3,
    "injection": 2,
}


# ---- 세상(inputs) ----


class CalendarEvent(BaseModel):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]+$")
    title: str
    date: date
    start: str = Field(default="10:00", pattern=r"^\d\d:\d\d$")  # 서울 HH:MM
    end: str = Field(default="11:00", pattern=r"^\d\d:\d\d$")


class WebResult(BaseModel):
    title: str
    link: str
    description: str


class PlaceDoc(BaseModel):
    """가짜 카카오 장소 하나. 영업시간·주차·가격 칸은 카카오 응답에 원래 없다."""

    name: str
    address: str
    category: str = ""
    phone: str = ""
    url: str = Field(pattern=r"^http://place\.map\.kakao\.com/\d+$")


class WeatherDay(BaseModel):
    min_c: float
    max_c: float
    rain_pct: int = Field(ge=0, le=100)
    code: int = 0  # WMO 날씨 코드(0 맑음, 61 약한 비 …)


class MailMessage(BaseModel):
    id: str = Field(pattern=r"^[0-9a-f]{16}$")
    sender: str
    subject: str
    received: datetime
    body: str


class EvalInputs(BaseModel):
    """사용자 메시지와, 그 메시지를 보낼 때의 세상."""

    message: str = Field(min_length=1, max_length=4000)
    now: datetime = EVAL_NOW
    calendar_events: list[CalendarEvent] = Field(default_factory=list)
    web_results: list[WebResult] | None = None
    places: list[PlaceDoc] | None = None
    weather: dict[date, WeatherDay] = Field(default_factory=dict)
    mail_messages: list[MailMessage] = Field(default_factory=list)
    seed_todos: list[str] = Field(default_factory=list)
    seed_memories: list[str] = Field(default_factory=list)


# ---- 기대(metadata) ----


class ArgCheck(BaseModel):
    """인자 값 하나의 검사. YAML에서 값만 쓰면 equals다."""

    model_config = ConfigDict(extra="forbid")

    equals: Any = None
    one_of: list[Any] | None = None
    pattern: str | None = None

    @model_validator(mode="before")
    @classmethod
    def from_shorthand(cls, value: Any) -> Any:
        if isinstance(value, dict) and value.keys() <= {"equals", "one_of", "pattern"}:
            return {key: as_text(item) for key, item in value.items()}
        return {"equals": as_text(value)}

    def matches(self, value: Any) -> bool:
        if "equals" in self.model_fields_set and value != self.equals:
            return False
        if self.one_of is not None and value not in self.one_of:
            return False
        return self.pattern is None or (value is not None and re.search(self.pattern, str(value)) is not None)


def as_text(value: Any) -> Any:
    """YAML이 날짜로 읽은 값(따옴표 없는 2026-11-19)을 도구 인자와 같은 문자열로 바꾼다."""
    if isinstance(value, list):
        return [as_text(item) for item in value]
    return value.isoformat() if isinstance(value, date) else value


type ArgSet = dict[str, ArgCheck]


class AnswerPath(BaseModel):
    """허용 정답 경로 하나."""

    name: str
    tools: list[str] = Field(default_factory=list)
    allowed: list[str] = Field(default_factory=list)
    clarify: bool = False
    args: dict[str, list[ArgSet]] = Field(default_factory=dict)

    @field_validator("args", mode="before")
    @classmethod
    def one_set_or_list(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return {tool: sets if isinstance(sets, list) else [sets] for tool, sets in value.items()}
        return value

    def matches_tools(self, called: set[str]) -> bool:
        return set(self.tools) <= called <= {*self.tools, *self.allowed}


class EvalMetadata(BaseModel):
    """기대(평가기가 보는 것)."""

    spec_no: int = Field(ge=1, le=30)  # BUILD_SPEC 16절 표의 번호
    category: Category
    paths: list[AnswerPath] = Field(min_length=1)
    forbidden_tools: list[str] = Field(default_factory=list)
    refusal: bool = False
    false_claims_any: list[str] = Field(default_factory=list)
    claims_all: list[str] = Field(default_factory=list)
    answer_keywords_any: list[str] = Field(default_factory=list)
    sources_required: bool = False
    private_markers: list[str] = Field(default_factory=list)
    answer_traits: str = ""

    @property
    def clarify_mode(self) -> Literal["required", "allowed", "never"]:
        """required: 되묻기만 정답. allowed: 되묻기나 답변만으로 끝나도 된다. never: 도구로 처리해야 한다."""
        if all(path.clarify for path in self.paths):
            return "required"
        if any(path.clarify or not path.tools for path in self.paths):
            return "allowed"
        return "never"

    @property
    def expects_approval(self) -> bool:
        """정답 경로 중 하나라도 승인이 필요한 도구를 부른다(예상 비용의 감시 에이전트 호출 수)."""
        return any(set(path.tools) & EXTERNAL_TOOL_NAMES for path in self.paths)


# ---- 출력 ----


class ModelUsage(BaseModel):
    """한 사례에서 모델 하나가 쓴 양(usage_logs를 모델·용도별로 합친 것)."""

    purpose: str  # agent(메인 에이전트) 또는 guard(감시 에이전트)
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    calls: int


class ToolCall(BaseModel):
    """실행되었거나 승인을 요청한 도구 호출 하나(steps 한 행)."""

    name: str
    args: dict[str, Any]
    status: str  # steps.status: done, failed, waiting_approval, …
    ok: bool | None
    approval_requested: bool


class EvalOutput(BaseModel):
    """사례 하나를 실행한 결과. 평가기는 이것만 본다."""

    task_status: str
    calls: list[ToolCall]
    attempted_tools: list[str] = Field(default_factory=list)  # 모델이 부르려 한 모든 도구(보이지 않던 도구 포함)
    final_answer: str | None
    external_writes: list[str] = Field(default_factory=list)  # 외부 서비스에 보낸 쓰기 요청 — 0이어야 한다
    outbound_requests: list[str] = Field(default_factory=list)  # 가짜 외부 API가 받은 모든 요청(쿼리 포함)
    served_urls: list[str] = Field(default_factory=list)  # 가짜 외부 API 응답에 들어 있던 링크(출처 검사)
    usage: list[ModelUsage] = Field(default_factory=list)

    @property
    def tools_called(self) -> list[str]:
        return [c.name for c in self.calls]

    @property
    def approvals_requested(self) -> list[str]:
        return [c.name for c in self.calls if c.approval_requested]

    @property
    def cost_usd(self) -> float:
        return round(sum(u.cost_usd for u in self.usage), 6)

    @property
    def total_tokens(self) -> int:
        return sum(u.input_tokens + u.output_tokens for u in self.usage)


type EvalDataset = Dataset[EvalInputs, EvalOutput, EvalMetadata]


def load_dataset(path: Path = DATASET_PATH) -> EvalDataset:
    from evals.evaluators import CUSTOM_EVALUATORS

    return Dataset[EvalInputs, EvalOutput, EvalMetadata].from_file(path, custom_evaluator_types=CUSTOM_EVALUATORS)
