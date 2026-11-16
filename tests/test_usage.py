"""8단계: 사용량 화면(/usage/). 로그인 필수, 본인 작업의 호출만, 집계 값, 빈 상태, 상한 도달 표시."""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

pytestmark = pytest.mark.django_db

USAGE_URL = "/usage/"


def make_log(task, cost, *, model="claude-haiku-5-5", purpose="agent", days_ago=0, **tokens):
    """UsageLog 한 줄. created_at은 auto_now_add라 만든 뒤 UPDATE로 날짜를 옮긴다."""
    from agent.models import UsageLog

    log = UsageLog.objects.create(task=task, purpose=purpose, model=model, cost_usd=Decimal(cost), **tokens)
    if days_ago:
        UsageLog.objects.filter(pk=log.pk).update(created_at=timezone.now() - timedelta(days=days_ago))
    return log


@pytest.fixture
def other_task(other_user):
    from agent.models import Task
    from chat.models import Conversation

    conversation = Conversation.objects.create(user=other_user, title="남의 대화")
    return Task.objects.create(user=other_user, conversation=conversation)


def test_usage_requires_login(client):
    response = client.get(USAGE_URL)
    assert response.status_code == 302
    assert reverse("accounts:login") in response["Location"]


def test_usage_url_name():
    assert reverse("agent:usage") == USAGE_URL


def test_settings_page_links_to_usage(logged_in_client):
    response = logged_in_client.get("/settings/")
    assert f'href="{USAGE_URL}"' in response.content.decode()


def test_empty_state(logged_in_client):
    response = logged_in_client.get(USAGE_URL)
    html = response.content.decode()
    assert response.status_code == 200
    assert "아직 기록된 사용량이 없습니다" in html
    assert "오늘 쓴 비용은" in html
    assert "모델별" not in html
    assert response.context["has_usage"] is False
    assert response.context["today"]["cost"] == 0
    assert len(response.context["daily"]) == 14


def test_only_my_usage_is_counted_and_shown(logged_in_client, task, other_task):
    make_log(task, "0.0100", input_tokens=100, output_tokens=20)
    make_log(other_task, "0.5000", model="claude-sonnet-5-5", input_tokens=9999)

    response = logged_in_client.get(USAGE_URL)
    html = response.content.decode()
    assert response.context["today"]["cost"] == Decimal("0.0100")
    assert response.context["today"]["calls"] == 1
    assert [m["model"] for m in response.context["by_model"]] == ["claude-haiku-5-5"]
    assert "claude-sonnet-5-5" not in html
    assert "$0.5000" not in html
    assert "9,999" not in html


def test_watch_logs_without_task_are_not_attributed(user, task):
    """조건 감시(task=None)는 누구의 호출인지 알 수 없어 어느 사용자의 화면에도 넣지 않는다."""
    from agent.usage import usage_summary

    make_log(None, "0.0300", purpose="watch")
    summary = usage_summary(user)
    assert summary["has_usage"] is False
    assert summary["today"]["cost"] == 0


def test_aggregates_by_day_and_model(settings, user, task):
    from agent.usage import usage_summary

    settings.DAILY_COST_LIMIT_USD = Decimal("1.0")
    make_log(task, "0.0100", input_tokens=100, output_tokens=20, cache_write_tokens=5, cache_read_tokens=3)
    make_log(task, "0.0023", purpose="guard", input_tokens=40, output_tokens=10)
    make_log(task, "0.2000", model="claude-sonnet-5-5", days_ago=1, input_tokens=1000, output_tokens=500)
    make_log(task, "0.0500", days_ago=3, input_tokens=10)
    make_log(task, "9.0000", days_ago=14, input_tokens=1)  # 14일 창 밖

    summary = usage_summary(user)
    today, yesterday, two_days_ago, three_days_ago = summary["daily"][:4]

    assert today["day"] == timezone.localdate()
    assert today["calls"] == 2
    assert today["cost"] == Decimal("0.0123")
    assert today["tokens"] == 100 + 20 + 5 + 3 + 40 + 10
    assert yesterday["calls"] == 1 and yesterday["cost"] == Decimal("0.2")
    assert two_days_ago == {**two_days_ago, "calls": 0, "tokens": 0, "cost": 0}
    assert three_days_ago["cost"] == Decimal("0.05")

    assert summary["today_percent"] == pytest.approx(1.23)
    assert summary["period_cost"] == Decimal("0.2623")
    assert summary["period_calls"] == 4
    # 막대는 가장 비싼 날(어제)을 100으로 둔다
    assert yesterday["bar_percent"] == 100
    assert three_days_ago["bar_percent"] == pytest.approx(25)

    by_model = {m["model"]: m for m in summary["by_model"]}
    assert list(by_model) == ["claude-sonnet-5-5", "claude-haiku-5-5"]  # 비싼 순
    assert by_model["claude-haiku-5-5"]["calls"] == 3
    assert by_model["claude-haiku-5-5"]["cost"] == Decimal("0.0623")
    assert by_model["claude-sonnet-5-5"]["tokens"] == 1500


def test_page_shows_formatted_numbers(logged_in_client, task):
    make_log(task, "0.0123", input_tokens=1200, output_tokens=34)
    html = logged_in_client.get(USAGE_URL).content.decode()
    assert "$0.0123" in html
    assert "1.2%" in html  # 하루 상한 $1 대비
    assert "1,234" in html
    assert "아직 기록된 사용량이 없습니다" not in html
    assert "claude-haiku-5-5" in html


def test_limit_reached_notice_uses_app_wide_total_without_showing_it(settings, logged_in_client, other_task):
    """상한은 앱 전체 합계로 걸리므로 멈췄다는 사실은 알리되, 남의 금액은 보여 주지 않는다."""
    settings.DAILY_COST_LIMIT_USD = Decimal("0.01")
    make_log(other_task, "0.0200")
    response = logged_in_client.get(USAGE_URL)
    html = response.content.decode()
    assert "오늘의 비용 상한에 도달했습니다" in html
    assert "$0.0200" not in html
    assert response.context["today"]["cost"] == 0


def test_no_limit_notice_under_limit(logged_in_client, task):
    make_log(task, "0.0100")
    assert "오늘의 비용 상한에 도달했습니다" not in logged_in_client.get(USAGE_URL).content.decode()


@pytest.mark.parametrize(
    ("value", "style", "expected"),
    [
        (Decimal("0"), "", "$0"),
        (Decimal("0.00004"), "", "<$0.0001"),
        (Decimal("0.00015"), "", "$0.0002"),
        (Decimal("0.183"), "", "$0.1830"),  # 장부에서 소수점 자리가 맞게 넷째 자리 고정
        (Decimal("1.0"), "short", "$1.00"),
        (Decimal("0.0125"), "short", "$0.0125"),
    ],
)
def test_usd_filter(value, style, expected):
    from agent.templatetags.usage_extras import usd

    assert usd(value, style) == expected


@pytest.mark.parametrize(
    ("value", "expected"), [(0, "0%"), (0.05, "0.1% 미만"), (1.23, "1.2%"), (100, "100%")]
)
def test_percent_filter(value, expected):
    from agent.templatetags.usage_extras import percent

    assert percent(value) == expected
