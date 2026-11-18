"""eval 첫 실행(2026-11-18) 실패 3건을 고친 시스템 프롬프트 규칙(docs/EVALS.md "첫 실행 실패 3건").

프롬프트 문장이 들어 있는지만 본다. 실제 모델이 따르는지는 eval을 다시 돌려 확인한다.
2026-11-18: BUILD_SPEC 16절 사례 24에서 감시만 조회한 회귀를 막기 위해 알림 목록 규칙을 검사한다.
"""

from app.agent.prompts import SYSTEM_PROMPT


def test_multiple_candidates_are_not_picked_by_the_model() -> None:
    """07: 같은 이름의 일정이 둘 이상이면 하나를 골라 승인 요청하지 않는다."""
    assert "하나를 골라 승인을 요청하지 않는다" in SYSTEM_PROMPT


def test_bulk_change_is_confirmed_before_approval() -> None:
    """08: 여러 일정을 한꺼번에 지우거나 바꾸는 요청은 목록을 보여 주고 먼저 확인받는다(승인 카드 재확인의 예외)."""
    assert "여러 일정을 한꺼번에 지우거나 바꾸라는 요청" in SYSTEM_PROMPT
    assert "승인 카드 재확인 금지의 예외" in SYSTEM_PROMPT


def test_searchable_request_is_searched_first() -> None:
    """13: 검색으로 바로 처리할 수 있는 요청은 되묻지 않고 먼저 검색한다."""
    assert "검색으로 바로 처리할 수 있는 요청은" in SYSTEM_PROMPT
    assert "되묻지 말고 먼저 검색한다" in SYSTEM_PROMPT


def test_notification_list_includes_schedules_and_watches() -> None:
    """24: 전체 알림 목록 안내에 두 조회 도구가 함께 있어야 한쪽을 빠뜨리지 않는다."""
    assert any(
        "알림 목록" in line and "list_schedules" in line and "list_watches" in line
        for line in SYSTEM_PROMPT.splitlines()
    )
