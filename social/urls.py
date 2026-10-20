from django.urls import path

from . import views

app_name = "social"

urlpatterns = [
    path("suivre/<str:username>/", views.follow_toggle, name="follow_toggle"),
    path("aimer/<uuid:pk>/", views.like_toggle, name="like_toggle"),
    path("commenter/<uuid:pk>/", views.comment_create, name="comment_create"),
    path("commentaires/<int:pk>/supprimer/", views.comment_delete, name="comment_delete"),
    path("notifications/", views.notifications, name="notifications"),
    path("notifications/tout-lire/", views.notifications_mark_all_read, name="notifications_mark_all_read"),
    path("notifications/compteur/", views.notifications_count, name="notifications_count"),
    path("notifications/<int:pk>/", views.notification_open, name="notification_open"),
    path("@<str:username>/abonnes/", views.follow_list, {"which": "abonnes"}, name="followers"),
    path("@<str:username>/abonnements/", views.follow_list, {"which": "abonnements"}, name="following"),
]
