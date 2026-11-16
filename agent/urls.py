from django.urls import path

from . import views

app_name = "agent"

urlpatterns = [
    path("tasks/", views.task_list, name="task_list"),
]
