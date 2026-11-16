from django.urls import path

from . import views

app_name = "agent"

urlpatterns = [
    path("tasks/", views.task_list, name="task_list"),
    path("approvals/<int:pk>/approve/", views.approve_approval, name="approve"),
    path("approvals/<int:pk>/reject/", views.reject_approval, name="reject"),
]
