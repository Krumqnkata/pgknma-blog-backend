from django.urls import path

from . import views


app_name = "valentine"

urlpatterns = [
    path("", views.login, name="login"),
    path("pin/", views.pin, name="pin"),
    path("home/", views.home, name="home"),
    path("change-pin/", views.change_pin, name="change_pin"),
    path("sessions/", views.active_sessions, name="sessions"),
    path("logout/", views.logout, name="logout"),
    path("students/<str:class_name>/", views.get_students, name="students"),
    path("inbox/", views.inbox, name="inbox"),
    path("archive/", views.received_archive, name="archive"),
    path("outbox/", views.outbox, name="outbox"),
    path(
        "outbox/<uuid:dispatch_id>/edit/",
        views.edit_valentine,
        name="edit",
    ),
    path(
        "report/<int:valentine_id>/",
        views.report_valentine,
        name="report",
    ),
    path("guide/", views.guide, name="guide"),
]
