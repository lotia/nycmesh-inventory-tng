"""Root URL configuration. See docs/architecture.md for the API layout."""

from django.contrib import admin
from django.urls import path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

from inventory.views import (
    ApiRootView,
    CategoryDetailView,
    CategoryListView,
    CurrentUserView,
    HealthCheckView,
    ItemDetailView,
    ItemListView,
    LabelListView,
    LabelResolveView,
    LocationDetailView,
    LocationListView,
    StockTransactionCreateView,
    VolunteerDetailView,
    VolunteerListCreateView,
)

urlpatterns = [
    path("admin/", admin.site.urls),
    # No trailing slash, matching every other endpoint below.
    path("api", ApiRootView.as_view(), name="api-root"),
    path("api/healthz", HealthCheckView.as_view(), name="healthz"),
    path("api/me", CurrentUserView.as_view(), name="me"),
    path("api/volunteers", VolunteerListCreateView.as_view(), name="volunteers"),
    path("api/volunteers/<int:pk>", VolunteerDetailView.as_view(), name="volunteer-detail"),
    path("api/items", ItemListView.as_view(), name="items"),
    path("api/items/<int:pk>", ItemDetailView.as_view(), name="item-detail"),
    path("api/locations", LocationListView.as_view(), name="locations"),
    path("api/locations/<int:pk>", LocationDetailView.as_view(), name="location-detail"),
    path("api/categories", CategoryListView.as_view(), name="categories"),
    path("api/categories/<int:pk>", CategoryDetailView.as_view(), name="category-detail"),
    path("api/labels", LabelListView.as_view(), name="labels"),
    # By code, not by id: the code is what is printed on the sticker, and it is
    # how a label is both resolved and revoked. See LabelResolveView.
    path("api/labels/<str:code>", LabelResolveView.as_view(), name="label-resolve"),
    path(
        "api/stock/transactions",
        StockTransactionCreateView.as_view(),
        name="stock-transactions",
    ),
    path("api/schema", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs", SpectacularSwaggerView.as_view(url_name="schema"), name="docs"),
]
