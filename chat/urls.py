from django.urls import path

from . import views

app_name = "chat"

urlpatterns = [
    path("", views.home, name="home"),
    path("c/<int:pk>/", views.conversation_detail, name="conversation"),
    path("c/<int:pk>/messages/", views.send_message, name="send_message"),
]
