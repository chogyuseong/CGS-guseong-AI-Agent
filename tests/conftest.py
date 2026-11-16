"""여러 테스트에서 함께 쓰는 준비물(fixture)."""

import pytest
from django.contrib.auth import get_user_model


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user(username="me", password="pw-for-test-1234")


@pytest.fixture
def other_user(db):
    return get_user_model().objects.create_user(username="someone", password="pw-for-test-1234")


@pytest.fixture
def logged_in_client(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def conversation(user):
    from chat.models import Conversation

    return Conversation.objects.create(user=user, title="테스트 대화")


@pytest.fixture
def task(user, conversation):
    from agent.models import Task

    return Task.objects.create(user=user, conversation=conversation)
