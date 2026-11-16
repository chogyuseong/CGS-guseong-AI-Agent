from django.urls import path

from . import views

app_name = "notifications"

urlpatterns = [
    path("push/subscribe/", views.subscribe, name="subscribe"),
    path("push/unsubscribe/", views.unsubscribe, name="unsubscribe"),
    path("sw.js", views.service_worker, name="service_worker"),
]
