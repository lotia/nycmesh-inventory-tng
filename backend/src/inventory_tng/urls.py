"""Root URL configuration. See docs/architecture.md for the API layout."""

from django.contrib import admin
from django.urls import path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

from inventory.views import healthz

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/healthz", healthz, name="healthz"),
    path("api/schema", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs", SpectacularSwaggerView.as_view(url_name="schema"), name="docs"),
]
