"""메시지 초안 도구 draft_message(4단계 보강, BUILD_SPEC 7.1·7.4). 등급 INTERNAL.

초안만 만든다. 메시지 발송은 금지 기능이라 이 앱에는 보내는 도구가 없다(절대 원칙 2).
- 본문은 모델이 쓰고 이 도구는 검증해 돌려준다(추가 모델 호출이 없어 따로 잡을 예산이 없다).
- 받는 사람은 화면 표시용 이름이다(전화번호·주소로 보내지 않는다). 본문과 따로 돌려준다.
- 결과에 sent=false와 "보내지 않았다"는 안내를 넣는다. 화면은 받는 사람·본문·복사 버튼을 보여 주고,
  보냈다는 문구를 쓰지 않는다(components/DraftCard.tsx).
- 외부로 아무것도 보내지 않으므로 승인이 필요 없다. 결과는 steps.output에 남는다(사용자가 쓴 글과 같은 취급).
"""

from collections.abc import Callable
from typing import Annotated, Any

from pydantic import Field
from pydantic_ai import RunContext

from app.agent.deps import AgentDeps
from app.agent.results import ToolResult
from app.tools.places import clip

Recipient = Annotated[
    str, Field(min_length=1, max_length=100, description="받는 사람 이름이나 호칭(표시용). 예: 민수, 팀장님")
]
DraftBody = Annotated[str, Field(min_length=1, max_length=2000, description="보낼 메시지 본문 초안")]

NOT_SENT_NOTICE = "보내지 않았습니다. 내용을 확인하고 복사해서 직접 보내 주세요."


async def draft_message(ctx: RunContext[AgentDeps], recipient: Recipient, body: DraftBody) -> ToolResult:
    """누군가에게 보낼 메시지의 초안을 만든다. 보내지 않는다(이 앱은 메시지를 보낼 수 없다).
    사용자에게는 초안을 확인하고 직접 복사해 보내라고 안내한다. 보냈다고 말하지 않는다."""
    return ToolResult.success(
        {"kind": "message_draft", "recipient": recipient, "body": body, "sent": False, "notice": NOT_SENT_NOTICE}
    )


LABELS: dict[str, Callable[[dict[str, Any]], str]] = {
    "draft_message": lambda args: f"{clip(args.get('recipient'), 20)}에게 보낼 초안 쓰는 중",
}
