from django.urls import path

from . import views

app_name = "agent"

urlpatterns = [
    path("tasks/", views.task_list, name="task_list"),
    path("usage/", views.usage_page, name="usage"),
    path("tasks/<int:pk>/steps/", views.task_steps, name="task_steps"),
    path("tasks/<int:pk>/cancel/", views.cancel_task, name="cancel_task"),
    path("approvals/<int:pk>/approve/", views.approve_approval, name="approve"),
    path("approvals/<int:pk>/reject/", views.reject_approval, name="reject"),
]
