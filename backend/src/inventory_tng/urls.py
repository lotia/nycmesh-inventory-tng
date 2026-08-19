"""Root URL configuration. See docs/architecture.md for the API layout."""

from django.contrib import admin
from django.urls import path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

from inventory.views import (
    ApiRootView,
    CategoryListView,
    HealthCheckView,
    ItemListView,
    LabelListView,
    LabelResolveView,
    LocationListView,
    StockTransactionCreateView,
    VolunteerListCreateView,
)

urlpatterns = [
    path("admin/", admin.site.urls),
    # No trailing slash, matching every other endpoint below.
    path("api", ApiRootView.as_view(), name="api-root"),
    path("api/healthz", HealthCheckView.as_view(), name="healthz"),
    path("api/volunteers", VolunteerListCreateView.as_view(), name="volunteers"),
    path("api/items", ItemListView.as_view(), name="items"),
    path("api/locations", LocationListView.as_view(), name="locations"),
    path("api/categories", CategoryListView.as_view(), name="categories"),
    path("api/labels", LabelListView.as_view(), name="labels"),
    path("api/labels/<str:code>", LabelResolveView.as_view(), name="label-resolve"),
    path(
        "api/stock/transactions",
        StockTransactionCreateView.as_view(),
        name="stock-transactions",
    ),
    path("api/schema", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs", SpectacularSwaggerView.as_view(url_name="schema"), name="docs"),
]
