"""배포(Railway) 설정: 환경변수가 없으면 지금 동작 그대로, 있으면 반영되는지 확인한다.

settings.py는 불러올 때 한 번 환경변수를 읽으므로, 환경변수를 바꾼 뒤 모듈을 다시 불러와(reload) 값을 본다.
다시 불러올 때 .env를 읽지 않게 막아, 내 PC의 .env 내용과 상관없이 같은 결과가 나오게 한다.
테스트가 끝나면 원래 환경으로 한 번 더 불러와 다른 테스트에 영향을 주지 않는다.
"""

import importlib

import dotenv
import pytest
from django.test import override_settings

DEPLOY_ENV_NAMES = [
    "CSRF_TRUSTED_ORIGINS",
    "SECURE_PROXY_SSL_HEADER",
    "SECURE_SSL_REDIRECT",
    "SECURE_HSTS_SECONDS",
    "SECURE_HSTS_INCLUDE_SUBDOMAINS",
]


@pytest.fixture
def load_settings(monkeypatch):
    import config.settings as module

    monkeypatch.setattr(dotenv, "load_dotenv", lambda *args, **kwargs: False)
    for name in DEPLOY_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)

    def _load(**env):
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        return importlib.reload(module)

    yield _load
    monkeypatch.undo()
    importlib.reload(module)


def test_https_settings_are_off_without_env(load_settings):
    s = load_settings()
    assert s.CSRF_TRUSTED_ORIGINS == []
    assert s.SECURE_PROXY_SSL_HEADER is None
    assert s.SECURE_SSL_REDIRECT is False
    assert s.SECURE_HSTS_SECONDS == 0
    assert s.SECURE_HSTS_INCLUDE_SUBDOMAINS is False


def test_https_settings_follow_env(load_settings):
    s = load_settings(
        CSRF_TRUSTED_ORIGINS="https://my-agent.up.railway.app, https://agent.example.com",
        SECURE_PROXY_SSL_HEADER="True",
        SECURE_SSL_REDIRECT="true",
        SECURE_HSTS_SECONDS="3600",
        SECURE_HSTS_INCLUDE_SUBDOMAINS="1",
    )
    assert s.CSRF_TRUSTED_ORIGINS == ["https://my-agent.up.railway.app", "https://agent.example.com"]
    assert s.SECURE_PROXY_SSL_HEADER == ("HTTP_X_FORWARDED_PROTO", "https")
    assert s.SECURE_SSL_REDIRECT is True
    assert s.SECURE_HSTS_SECONDS == 3600
    assert s.SECURE_HSTS_INCLUDE_SUBDOMAINS is True


def test_secure_cookies_follow_debug(load_settings):
    assert load_settings(DEBUG="False").SESSION_COOKIE_SECURE is True
    assert load_settings(DEBUG="True").CSRF_COOKIE_SECURE is False


def test_test_run_keeps_local_behavior(settings):
    """테스트(=로컬과 같은 http)에서는 HTTPS 강제가 꺼져 있어야 기존 테스트가 그대로 돈다."""
    assert settings.SECURE_SSL_REDIRECT is False
    assert settings.SECURE_PROXY_SSL_HEADER is None
    assert settings.SECURE_HSTS_SECONDS == 0


def test_whitenoise_comes_right_after_security_middleware(settings):
    middleware = list(settings.MIDDLEWARE)
    security = middleware.index("django.middleware.security.SecurityMiddleware")
    assert middleware[security + 1] == "whitenoise.middleware.WhiteNoiseMiddleware"


def test_deploy_uses_hashed_compressed_static_storage():
    import config.settings as module

    backend = module.STORAGES["staticfiles"]["BACKEND"]
    assert backend == "whitenoise.storage.CompressedManifestStaticFilesStorage"


def test_tests_use_plain_static_storage(settings):
    """collectstatic 없이 템플릿의 {% static %}이 오류 없이 그려지게."""
    backend = settings.STORAGES["staticfiles"]["BACKEND"]
    assert backend == "django.contrib.staticfiles.storage.StaticFilesStorage"


@pytest.mark.parametrize(
    "path, content_type",
    [
        ("/static/css/app.css", "text/css"),
        ("/static/manifest.webmanifest", "application/manifest+json"),
        ("/static/icons/icon-192.png", "image/png"),
        ("/static/vendor/htmx.min.js", "text/javascript"),
    ],
)
def test_whitenoise_serves_static_files(client, path, content_type):
    response = client.get(path)
    assert response.status_code == 200
    assert response["Content-Type"].startswith(content_type)


def test_service_worker_stays_at_root(client):
    """서비스 워커는 /static/이 아니라 /sw.js로 나가야 사이트 전체를 맡는다(WhiteNoise와 무관하게 유지)."""
    response = client.get("/sw.js")
    assert response.status_code == 200
    assert response["Content-Type"].startswith("application/javascript")


@override_settings(SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO", "https"), SECURE_SSL_REDIRECT=True)
def test_proxy_header_prevents_redirect_loop(client):
    """프록시가 https라고 알려 준 요청은 그대로 처리하고, http 요청만 https로 돌려보낸다."""
    behind_proxy = client.get("/accounts/login/", headers={"X-Forwarded-Proto": "https"})
    assert behind_proxy.status_code == 200

    plain_http = client.get("/accounts/login/")
    assert plain_http.status_code == 301
    assert plain_http["Location"].startswith("https://")


@pytest.mark.django_db
@override_settings(
    SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO", "https"),
    CSRF_TRUSTED_ORIGINS=["https://my-agent.up.railway.app"],
    ALLOWED_HOSTS=["my-agent.up.railway.app"],
)
def test_login_post_from_deployed_origin_passes_csrf():
    """배포 주소에서 보낸 로그인 POST가 CSRF 검사(Origin 확인)를 통과하는지 본다."""
    from django.test import Client

    client = Client(enforce_csrf_checks=True)
    headers = {"Host": "my-agent.up.railway.app", "X-Forwarded-Proto": "https"}
    page = client.get("/accounts/login/", headers=headers)
    token = page.cookies["csrftoken"].value
    response = client.post(
        "/accounts/login/",
        {"username": "nobody", "password": "wrong", "csrfmiddlewaretoken": token},
        headers={**headers, "Origin": "https://my-agent.up.railway.app"},
    )
    assert response.status_code == 200  # CSRF 실패면 403. 계정이 없으니 로그인 화면이 다시 나온다.

    forged = client.post(
        "/accounts/login/",
        {"username": "nobody", "password": "wrong", "csrfmiddlewaretoken": token},
        headers={**headers, "Origin": "https://evil.example.com"},
    )
    assert forged.status_code == 403
