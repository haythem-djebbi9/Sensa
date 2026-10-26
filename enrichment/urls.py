from django.urls import path

from . import views, views_gestion as gestion

app_name = "enrichment"

urlpatterns = [
    # Tableau de bord et pipeline
    path("", views.monitor, name="monitor"),
    path("analyses/<int:pk>/", views.analysis_detail, name="analysis_detail"),
    path("analyses/<int:pk>/relancer/", views.retry_analysis, name="retry_analysis"),
    path("analyses/relancer-echecs/", views.retry_all_failed, name="retry_all_failed"),
    path("chien-de-garde/", views.run_watchdog, name="watchdog"),
    path("configuration/", views.update_config, name="config"),
    path("publications/<uuid:pk>/reanalyser/", views.reenrich, name="reenrich"),
    path("audio/<int:pk>/sous-titres.<str:fmt>", views.subtitles, name="subtitles"),
    path("audio/<int:pk>/traduire/", views.translate, name="translate"),
    # Correction humaine des transcriptions
    path("audio/<int:pk>/transcription/", gestion.transcription_edit, name="transcription_edit"),
    path("audio/<int:pk>/transcription/restaurer/", gestion.transcription_revert, name="transcription_revert"),
    # Tags (CRUD + fusion)
    path("tags/", gestion.tag_list, name="tags"),
    path("tags/nouveau/", gestion.tag_create, name="tag_create"),
    path("tags/<int:pk>/", gestion.tag_detail, name="tag_detail"),
    path("tags/<int:pk>/modifier/", gestion.tag_update, name="tag_update"),
    path("tags/<int:pk>/fusionner/", gestion.tag_merge, name="tag_merge"),
    path("tags/<int:pk>/supprimer/", gestion.tag_delete, name="tag_delete"),
    # Vocabulaire de transcription (CRUD)
    path("vocabulaire/", gestion.vocabulary_list, name="vocabulary"),
    path("vocabulaire/nouveau/", gestion.vocabulary_create, name="vocabulary_create"),
    path("vocabulaire/<int:pk>/modifier/", gestion.vocabulary_update, name="vocabulary_update"),
    path("vocabulaire/<int:pk>/basculer/", gestion.vocabulary_toggle, name="vocabulary_toggle"),
    path("vocabulaire/<int:pk>/supprimer/", gestion.vocabulary_delete, name="vocabulary_delete"),
    # Prompts IA versionnés (CRUD)
    path("prompts/", gestion.prompt_list, name="prompts"),
    path("prompts/nouveau/", gestion.prompt_create, name="prompt_create"),
    path("prompts/<int:pk>/modifier/", gestion.prompt_update, name="prompt_update"),
    path("prompts/<int:pk>/activer/", gestion.prompt_activate, name="prompt_activate"),
    path("prompts/<int:pk>/supprimer/", gestion.prompt_delete, name="prompt_delete"),
    path("prompts/<str:task>/defaut/", gestion.prompt_use_default, name="prompt_default"),
    # Laboratoire IA
    path("laboratoire/", gestion.laboratory, name="laboratory"),
    # Digests IA (CRUD)
    path("digests/", gestion.digest_list, name="digests"),
    path("digests/generer/", gestion.digest_create, name="digest_create"),
    path("digests/<int:pk>/", gestion.digest_detail, name="digest_detail"),
    path("digests/<int:pk>/regenerer/", gestion.digest_regenerate, name="digest_regenerate"),
    path("digests/<int:pk>/supprimer/", gestion.digest_delete, name="digest_delete"),
]
