from django.urls import path

from . import views

app_name = "automation"

urlpatterns = [
    path("schedules/", views.schedule_list, name="schedule_list"),
    path("schedules/<int:pk>/toggle/", views.schedule_toggle, name="schedule_toggle"),
    path("schedules/<int:pk>/delete/", views.schedule_delete, name="schedule_delete"),
    path("watches/", views.watch_list, name="watch_list"),
    path("watches/<int:pk>/toggle/", views.watch_toggle, name="watch_toggle"),
    path("watches/<int:pk>/delete/", views.watch_delete, name="watch_delete"),
]
