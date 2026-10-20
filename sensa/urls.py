from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

admin.site.site_header = "Sensa · administration technique"
admin.site.site_title = "Sensa"

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("accounts.urls")),
    path("", include("feed.urls")),
    path("publications/", include("publications.urls")),
    path("social/", include("social.urls")),
    path("ia/", include("enrichment.urls")),
    path("moderation/", include("moderation.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
