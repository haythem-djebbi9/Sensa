from django.urls import path

from . import views

app_name = "publications"

urlpatterns = [
    path("nouvelle/", views.create, name="create"),
    path("suggerer-texte-alternatif/", views.suggest_alt_text, name="suggest_alt_text"),
    path("<uuid:pk>/", views.detail, name="detail"),
    path("<uuid:pk>/carte/", views.card, name="card"),
    path("<uuid:pk>/modifier/", views.edit, name="edit"),
    path("<uuid:pk>/supprimer/", views.delete, name="delete"),
    path("<uuid:pk>/relancer/", views.retry, name="retry"),
    path("<uuid:pk>/republier/", views.republish_toggle, name="republish_toggle"),
    path("<uuid:pk>/enregistrer/", views.save_toggle, name="save_toggle"),
]
