from django.urls import path

from . import views

app_name = "feed"

urlpatterns = [
    path("", views.home, name="home"),
    path("recherche/", views.search, name="search"),
    path("tags/<str:name>/", views.tag, name="tag"),
]
