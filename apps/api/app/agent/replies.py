"""6단계 보강: 예약 작업의 구조화된 답(BUILD_SPEC 8.3, 11.1 AgentReply의 suggestions 부분).

예약 작업(origin=schedule)은 agent.run의 output_type에 AutomationReply를 더해 실행한다(runner.output_type_for).
- text: 대화방에 남길 답(messages.content). suggestions: 사용자가 눌러 입력창에 채울 요청 문장(messages.suggestions).
- 제안은 문장일 뿐이다. 누르면 입력창에 채워지고 사용자가 보내야 평소 채팅처럼 실행·승인을 거친다
  (승인·권한을 넘기지 않는다).
- 모델이 그냥 글로 답해도(str) 실행은 끝난다. 그때는 제안이 없다.
채팅 task의 출력 형식(str)은 바꾸지 않았다. 출처·되묻기까지 담는 AgentReply 전체는 docs/STATUS.md에 남은 일로 둔다.

흐름: runner.output_type_for(schedule) → 모델이 AutomationReply로 답함 → runner가 text를 final_answer·메시지 본문으로,
suggestion_rows()를 messages.suggestions(JSONB)로 저장 → API는 app/schemas/conversations.py의
SuggestionOut으로 내보낸다.
근거: DECISIONS 2026-11-17("구조화된 제안", "AgentReply 구조화 출력은 미룬다"), 6단계 보강.

고칠 때 주의:
- Field의 description·제한은 모델에게 보이는 출력 스키마다. 바꾸면 모델 답이 달라지므로 eval을 다시 돌려 본다.
- Suggestion에 필드를 더하거나 이름을 바꾸면 SuggestionOut, 웹 화면(pnpm --dir apps/web gen:api 후 제안 버튼),
  tests/test_suggestions.py를 함께 고친다. 이미 저장된 messages.suggestions 행은 옛 모양 그대로다.
"""

from pydantic import BaseModel, Field

# 한 번의 예약 답에 붙일 제안 수 상한(DECISIONS 2026-11-17 "구조화된 제안: ... 최대 5개"). 넘으면 출력 검증 실패로
# Pydantic AI가 모델에게 다시 요청한다.
MAX_SUGGESTIONS = 5


class Suggestion(BaseModel):
    """제안 버튼 하나. 누르면 proposed_user_message가 입력창에 채워질 뿐 바로 실행되지 않는다."""

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
        """messages.suggestions에 넣을 값. 제안이 없으면 빈 목록 대신 None(다른 메시지와 같게 null로 저장)."""
        return [s.model_dump() for s in self.suggestions] or None
