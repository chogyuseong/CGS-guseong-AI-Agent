from django.contrib import admin
from django.urls import include, path

from accounts.urls import settings_urlpatterns

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("accounts.urls")),
    path("", include((settings_urlpatterns, "settings_page"))),
    path("", include("chat.urls")),
    path("", include("agent.urls")),
    path("", include("memory.urls")),
    path("", include("automation.urls")),
    path("", include("notifications.urls")),
]
