from django.urls import path

from . import views


app_name = "valentine"

urlpatterns = [
    path("", views.login, name="login"),
    path("home/", views.home, name="home"),
    path("logout/", views.logout, name="logout"),
    path("students/<str:class_name>/", views.get_students, name="students"),
    path("inbox/", views.inbox, name="inbox"),
    path("outbox/", views.outbox, name="outbox"),
    path(
        "report/<int:valentine_id>/",
        views.report_valentine,
        name="report",
    ),
    path("guide/", views.guide, name="guide"),
]
