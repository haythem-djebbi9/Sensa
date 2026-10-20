from django.urls import path

from . import views

app_name = "moderation"

urlpatterns = [
    path("", views.queue, name="queue"),
    path("decision/<uuid:pk>/", views.decide, name="decide"),
    path("signaler/<uuid:pk>/", views.report, name="report"),
    path("parametres/", views.admin_settings, name="settings"),
    path("comptes/", views.accounts, name="accounts"),
    path("comptes/<uuid:pk>/", views.account_update, name="account_update"),
]
