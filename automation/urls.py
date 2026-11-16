from django.urls import path

from . import views

app_name = "automation"

urlpatterns = [
    path("schedules/", views.schedule_list, name="schedule_list"),
]
