"""
테스트는 실제 외부 API를 부르지 않는다(비용 절감 6번).
.env에 진짜 키가 있어도 테스트 설정이 키를 비우므로, 가짜 LLM을 깜빡해도 과금 호출이 나가지 않는다.
"""

import pytest
from django.conf import settings

SECRET_SETTINGS = [
    "ANTHROPIC_API_KEY",
    "KAKAO_REST_API_KEY",
    "NAVER_CLIENT_ID",
    "NAVER_CLIENT_SECRET",
    "GOOGLE_CLIENT_ID",
    "GOOGLE_CLIENT_SECRET",
    "TOKEN_ENCRYPTION_KEY",
    "VAPID_PRIVATE_KEY",
]


@pytest.mark.parametrize("name", SECRET_SETTINGS)
def test_real_secrets_are_not_visible_in_tests(name):
    assert getattr(settings, name) == ""


def test_real_llm_client_cannot_be_created_in_tests():
    from agent.llm import AnthropicClient, LLMNotConfigured

    with pytest.raises(LLMNotConfigured):
        AnthropicClient()
