"""0단계: 데이터 모델의 기본값과 DB 제약이 명세대로인지 확인한다."""

from datetime import timedelta

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

pytestmark = pytest.mark.django_db


def test_task_starts_queued_from_chat(task):
    from agent.models import Task

    assert task.status == Task.Status.QUEUED
    assert task.origin == Task.Origin.CHAT
    assert task.step_count == 0
    assert task.total_tokens == 0


def test_step_order_is_unique_per_task(task):
    from agent.models import RiskLevel, Step

    Step.objects.create(task=task, order=1, tool_name="add_todo", risk_level=RiskLevel.INTERNAL)
    with pytest.raises(IntegrityError), transaction.atomic():
        Step.objects.create(task=task, order=1, tool_name="list_todos", risk_level=RiskLevel.READ)


def test_step_keeps_risk_level_and_starts_pending(task):
    from agent.models import RiskLevel, Step

    step = Step.objects.create(task=task, order=1, tool_name="create_event", risk_level=RiskLevel.EXTERNAL)
    step.refresh_from_db()
    assert step.risk_level == "EXTERNAL"
    assert step.status == Step.Status.PENDING
    assert step.ok is None


def test_approval_expires_30_minutes_after_creation(task):
    from agent.models import Approval, RiskLevel, Step

    step = Step.objects.create(task=task, order=1, tool_name="create_event", risk_level=RiskLevel.EXTERNAL)
    approval = Approval.objects.create(step=step, summary="일정을 만듭니다.")
    expected = timezone.now() + timedelta(minutes=30)
    assert approval.status == Approval.Status.PENDING
    assert abs((approval.expires_at - expected).total_seconds()) < 5


def test_connection_is_unique_per_user_and_provider(user):
    from accounts.models import Connection

    Connection.objects.create(user=user, provider=Connection.Provider.GOOGLE, encrypted_token="x")
    with pytest.raises(IntegrityError), transaction.atomic():
        Connection.objects.create(user=user, provider=Connection.Provider.GOOGLE, encrypted_token="y")


def test_watch_interval_below_30_minutes_is_rejected_by_db(user):
    from automation.models import Watch

    with pytest.raises(IntegrityError), transaction.atomic():
        Watch.objects.create(
            user=user,
            description="비 오면 알려줘",
            tool_name="get_weather",
            condition_text="강수 확률 60% 이상",
            interval_minutes=10,
            next_run_at=timezone.now(),
        )


def test_watch_defaults_expire_in_30_days_without_repeat(user):
    from automation.models import Watch

    watch = Watch.objects.create(
        user=user,
        description="비 오면 알려줘",
        tool_name="get_weather",
        condition_text="강수 확률 60% 이상",
        next_run_at=timezone.now(),
    )
    assert watch.interval_minutes == 30
    assert watch.repeat is False
    assert watch.is_active is True
    assert (watch.expires_at - timezone.now()).days in (29, 30)


def test_push_endpoint_is_unique(user, other_user):
    from notifications.models import PushSubscription

    PushSubscription.objects.create(user=user, endpoint="https://push.example/1", p256dh="k", auth="a")
    with pytest.raises(IntegrityError), transaction.atomic():
        PushSubscription.objects.create(
            user=other_user, endpoint="https://push.example/1", p256dh="k", auth="a"
        )


def test_message_links_to_task_through_messages(conversation, task):
    from chat.models import Message

    Message.objects.create(conversation=conversation, role=Message.Role.USER, content="안녕", task=task)
    assert task.messages.count() == 1


def test_every_model_has_timestamps(user, conversation):
    from chat.models import Message
    from memory.models import Memory
    from tools.models import Note, Todo

    message = Message.objects.create(conversation=conversation, role=Message.Role.USER, content="기억해")
    for obj in [
        Memory.objects.create(user=user, content="커피는 라떼", source_message=message),
        Note.objects.create(user=user, content="메모"),
        Todo.objects.create(user=user, title="우유 사기"),
    ]:
        assert obj.created_at is not None
        assert obj.updated_at is not None
