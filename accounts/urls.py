from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    # 회원가입은 만들지 않는다. 계정은 createsuperuser로만 만든다.
    path("login/", auth_views.LoginView.as_view(template_name="accounts/login.html"), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
]

# 명세의 URL(/settings/, /connections/...)은 /accounts/ 접두사 없이 루트에 연결한다.
settings_urlpatterns = [
    path("settings/", views.settings_page, name="settings"),
    path("settings/briefing/", views.briefing_update, name="briefing"),
    path("connections/", views.connections_page, name="connections"),
    path("connections/google/start/", views.google_start, name="google_start"),
    path("connections/google/callback/", views.google_callback, name="google_callback"),
    path("connections/google/disconnect/", views.google_disconnect, name="google_disconnect"),
]
