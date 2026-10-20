from django.urls import path

from . import views

app_name = "enrichment"

urlpatterns = [
    path("", views.monitor, name="monitor"),
    path("analyses/<int:pk>/", views.analysis_detail, name="analysis_detail"),
    path("analyses/<int:pk>/relancer/", views.retry_analysis, name="retry_analysis"),
    path("analyses/relancer-echecs/", views.retry_all_failed, name="retry_all_failed"),
    path("chien-de-garde/", views.run_watchdog, name="watchdog"),
    path("configuration/", views.update_config, name="config"),
    path("publications/<uuid:pk>/reanalyser/", views.reenrich, name="reenrich"),
    path("audio/<int:pk>/sous-titres.<str:fmt>", views.subtitles, name="subtitles"),
    path("audio/<int:pk>/traduire/", views.translate, name="translate"),
]
