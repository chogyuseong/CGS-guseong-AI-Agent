"""6단계 보강: 예약 작업의 구조화된 답(BUILD_SPEC 8.3, 11.1 AgentReply의 suggestions 부분).

예약 작업(origin=schedule)은 agent.run의 output_type에 AutomationReply를 더해 실행한다(runner.output_type_for).
- text: 대화방에 남길 답(messages.content). suggestions: 사용자가 눌러 입력창에 채울 요청 문장(messages.suggestions).
- 제안은 문장일 뿐이다. 누르면 입력창에 채워지고 사용자가 보내야 평소 채팅처럼 실행·승인을 거친다
  (승인·권한을 넘기지 않는다).
- 모델이 그냥 글로 답해도(str) 실행은 끝난다. 그때는 제안이 없다.
채팅 task의 출력 형식(str)은 바꾸지 않았다. 출처·되묻기까지 담는 AgentReply 전체는 docs/STATUS.md에 남은 일로 둔다.
"""

from pydantic import BaseModel, Field

MAX_SUGGESTIONS = 5


class Suggestion(BaseModel):
    title: str = Field(min_length=1, max_length=40, description="버튼에 보일 짧은 이름. 예: 우산 챙기기")
    proposed_user_message: str = Field(
        min_length=1,
        max_length=300,
        description="사용자가 보낼 요청 문장 그대로. 예: 내일 아침 '우산 챙기기' 할 일 추가해줘",
    )
    reason: str = Field(default="", max_length=200, description="왜 제안하는지 한 문장(조회 결과의 사실만)")


class AutomationReply(BaseModel):
    """예약 작업의 최종 답. 행동이 필요한 항목이 없으면 suggestions는 빈 목록."""

    text: str = Field(min_length=1, max_length=4000, description="사용자에게 보일 요약")
    suggestions: list[Suggestion] = Field(default_factory=list, max_length=MAX_SUGGESTIONS)

    def suggestion_rows(self) -> list[dict[str, str]] | None:
        return [s.model_dump() for s in self.suggestions] or None
