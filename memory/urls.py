from django.urls import path

from . import views

app_name = "memory"

urlpatterns = [
    path("memory/", views.memory_list, name="list"),
]
