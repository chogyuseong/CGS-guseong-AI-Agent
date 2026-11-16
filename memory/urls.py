from django.urls import path

from . import views

app_name = "memory"

urlpatterns = [
    path("memory/", views.memory_list, name="list"),
    path("memory/<int:pk>/delete/", views.memory_delete, name="delete"),
    path("memory/delete-all/", views.memory_delete_all, name="delete_all"),
]
