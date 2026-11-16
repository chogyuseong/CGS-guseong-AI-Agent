from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    # 회원가입은 만들지 않는다. 계정은 createsuperuser로만 만든다.
    path("login/", auth_views.LoginView.as_view(template_name="accounts/login.html"), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
]

# 명세의 URL은 /settings/ 이므로 /accounts/ 접두사 없이 따로 연결한다.
settings_urlpatterns = [
    path("settings/", views.settings_page, name="settings"),
]
