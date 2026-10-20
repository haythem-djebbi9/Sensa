from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("comptes/inscription/", views.signup, name="signup"),
    path("comptes/connexion/", views.SensaLoginView.as_view(), name="login"),
    path("comptes/deconnexion/", auth_views.LogoutView.as_view(), name="logout"),
    path("comptes/confirmer/renvoyer/", views.resend_confirmation, name="resend_confirmation"),
    path("comptes/confirmer/<uidb64>/<token>/", views.confirm_email, name="confirm_email"),
    path("comptes/mot-de-passe/oublie/", views.password_reset, name="password_reset"),
    path("comptes/mot-de-passe/envoye/", views.password_reset_done, name="password_reset_done"),
    path("comptes/mot-de-passe/termine/", views.password_reset_complete, name="password_reset_complete"),
    path("comptes/mot-de-passe/<uidb64>/<token>/", views.password_reset_confirm, name="password_reset_confirm"),
    path("comptes/profil/", views.profile_edit, name="profile_edit"),
    path("@<str:username>/", views.profile, name="profile"),
]
