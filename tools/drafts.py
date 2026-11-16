"""
draft_message: 누군가에게 보낼 글의 초안(INTERNAL). 보내는 기능은 없다(절대 원칙 2).

명세의 입력(recipient_label, purpose, tone)에 draft를 더했다: 초안 글은 모델이 쓰고,
도구는 그것을 '보내지 않은 초안'으로 표시해 화면이 복사 상자로 보여 주게 한다(DECISIONS.md).
어디에도 저장하거나 전송하지 않는다.
"""

from agent.models import RiskLevel
from agent.registry import Tool, ok_result, register

NOT_SENT = "보내지 않았습니다. 화면의 복사 버튼으로 복사해 사용자가 직접 보냅니다."


def draft_message(user, recipient_label, purpose, tone, draft):
    return ok_result({
        "recipient_label": recipient_label,
        "purpose": purpose,
        "tone": tone,
        "draft": draft.strip(),
        "note": NOT_SENT,
    })  # fmt: skip


register(
    Tool(
        name="draft_message",
        description=(
            "누군가에게 보낼 메시지나 메일 답장의 초안을 만든다. draft에 완성된 초안 글을 넣는다. "
            "보내는 기능은 없고, 화면이 초안을 복사 버튼과 함께 보여 준다. "
            "답변에 초안 전문을 다시 쓰지 않는다."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "recipient_label": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 50,
                    "description": "받는 사람(예: 김 팀장님)",
                },
                "purpose": {"type": "string", "minLength": 1, "maxLength": 200},
                "tone": {"type": "string", "maxLength": 50, "description": "예: 정중하게, 편하게"},
                "draft": {"type": "string", "minLength": 1, "maxLength": 3000},
            },
            "required": ["recipient_label", "purpose", "tone", "draft"],
            "additionalProperties": False,
        },
        risk_level=RiskLevel.INTERNAL,
        run=draft_message,
        label=lambda recipient_label="", **_: f"초안 작성: {recipient_label}에게",
    )
)
