import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints

from app.automation.rules import AUTOMATION_CONVERSATION_TITLE
from app.models.enums import MessageRole

DEFAULT_CONVERSATION_TITLE = "새 대화"
MAX_MESSAGE_LENGTH = 4000


def not_automation_title(title: str) -> str:
    """'예약 작업' 대화방은 서버가 제목으로 찾는다(app/automation/store.py). 사용자가 같은 제목의 대화를 만들면
    자동 실행 결과가 그 대화로 들어갈 수 있어서 막는다(8단계, docs/DECISIONS.md)."""
    if title == AUTOMATION_CONVERSATION_TITLE:
        raise ValueError(f"'{AUTOMATION_CONVERSATION_TITLE}'은 자동 실행 결과용 대화방 이름이라 쓸 수 없습니다.")
    return title


# 앞뒤 공백을 지운 뒤 1~4000자. 공백만 보내면 422.
MessageText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_MESSAGE_LENGTH)]
TitleText = Annotated[
    str, StringConstraints(strip_whitespace=True, max_length=200), AfterValidator(not_automation_title)
]
# 메시지가 어디서 왔는가: 채팅(사용자 메시지와 그 답변), 예약 작업 결과, 조건 감시 알림.
MessageOrigin = Literal["chat", "schedule", "watch"]


class ConversationCreate(BaseModel):
    # 비우거나 생략하면 '새 대화'. '예약 작업'은 쓸 수 없다(422).
    title: TitleText | None = None


class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    created_at: datetime
    updated_at: datetime


class MessageCreate(BaseModel):
    content: MessageText
    # 화면이 보낼 때마다 새로 만드는 UUID. 네트워크 오류로 다시 보낼 때는 같은 값을 쓴다(중복 task 방지).
    client_request_id: uuid.UUID


class SuggestionOut(BaseModel):
    title: str
    proposed_user_message: str
    reason: str = ""


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    role: MessageRole
    content: str
    task_id: int | None
    created_at: datetime
    # 8단계 추가(기존 필드는 그대로). 화면이 모양으로 추측하지 않고 출처를 알 수 있게 한다.
    # chat: 사용자 메시지와 채팅 task의 답변 / schedule: 예약 작업 task의 답변 / watch: 조건 감시 알림(task 없음)
    origin: MessageOrigin
    # 조건 감시 알림이 근거로 삼은 출처 링크(감시 도구 결과의 sources). 그 밖의 메시지는 빈 목록.
    # 외부 서비스가 준 주소이므로 화면은 http·https만 링크로 만든다.
    sources: list[str]
    # 6단계 보강: 예약 작업 답의 제안. 누르면 입력창에 proposed_user_message를 채운다(보내기는 사용자). 없으면 빈 목록.
    suggestions: list[SuggestionOut] = Field(default_factory=list)


class TaskCreated(BaseModel):
    """메시지 전송 응답(202). 화면은 이 task_id로 GET /api/tasks/{id}를 조회한다."""

    task_id: int
