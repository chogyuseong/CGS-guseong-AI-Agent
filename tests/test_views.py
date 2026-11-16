"""0단계: 로그인 필수, 내 대화만 보기, 하단 탭 화면이 열리는지 확인한다."""

import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db

TAB_URLS = ["/tasks/", "/schedules/", "/memory/", "/settings/"]


def test_login_page_opens(client):
    response = client.get(reverse("accounts:login"))
    assert response.status_code == 200


def test_anonymous_user_is_sent_to_login(client):
    response = client.get("/")
    assert response.status_code == 302
    assert reverse("accounts:login") in response["Location"]


@pytest.mark.parametrize("url", TAB_URLS)
def test_tab_pages_require_login(client, url):
    response = client.get(url)
    assert response.status_code == 302


def test_home_creates_first_conversation_and_redirects(logged_in_client, user):
    from chat.models import Conversation

    response = logged_in_client.get("/")
    conversation = Conversation.objects.get(user=user)
    assert response.status_code == 302
    assert response["Location"] == f"/c/{conversation.id}/"


def test_home_reuses_most_recent_conversation(logged_in_client, conversation):
    response = logged_in_client.get("/")
    assert response["Location"] == f"/c/{conversation.id}/"


def test_chat_page_shows_bottom_tabs(logged_in_client, conversation):
    response = logged_in_client.get(f"/c/{conversation.id}/")
    body = response.content.decode()
    assert response.status_code == 200
    for label in ["채팅", "작업 기록", "자동화", "기억", "설정"]:
        assert label in body


def test_cannot_open_someone_elses_conversation(client, other_user, conversation):
    client.force_login(other_user)
    response = client.get(f"/c/{conversation.id}/")
    assert response.status_code == 404


@pytest.mark.parametrize("url", TAB_URLS)
def test_tab_pages_open_when_logged_in(logged_in_client, url):
    response = logged_in_client.get(url)
    assert response.status_code == 200


@pytest.mark.parametrize(
    "path", ["manifest.webmanifest", "icons/icon-192.png", "icons/icon-512.png", "css/app.css"]
)
def test_pwa_static_files_exist(path):
    from django.contrib.staticfiles import finders

    assert finders.find(path) is not None


def test_pages_link_the_manifest(client):
    body = client.get(reverse("accounts:login")).content.decode()
    assert "manifest.webmanifest" in body
