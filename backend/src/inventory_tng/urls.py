"""Root URL configuration. See docs/architecture.md for the API layout."""

from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerSplitView

from inventory.views import (
    ApiRootView,
    CategoryDetailView,
    CategoryListView,
    CurrentUserView,
    DebugTraceVerifyView,
    HealthCheckView,
    ItemDetailView,
    ItemListView,
    LabelListView,
    LabelResolveView,
    LabelSheetView,
    LocationDetailView,
    LocationListView,
    StockTransactionCreateView,
    VolunteerDetailView,
    VolunteerListCreateView,
)

urlpatterns = [
    # Before the admin's own patterns, and so instead of its login form.
    #
    # Decision 0013's "two sign-in surfaces exist and must agree": the admin
    # ships a password form of its own that knows nothing about providers or
    # second factors, so leaving it in place would leave a way in that walks
    # straight past point 3. The admin still sends people here under the name
    # it reverses, `admin:login`; what answers to that name is now allauth.
    #
    # query_string carries the `?next=` the admin appended, so somebody who
    # asked for a page inside the admin still lands on it afterwards.
    path(
        "admin/login/",
        RedirectView.as_view(pattern_name="account_login", query_string=True),
        name="admin-login",
    ),
    path("admin/", admin.site.urls),
    # Every way in, from one dependency: the local password form, the
    # providers a deployment configured, and the second factors.
    # See docs/decisions/0013-administrator-sign-in.md.
    path("accounts/", include("allauth.urls")),
    # No trailing slash, matching every other endpoint below.
    path("api", ApiRootView.as_view(), name="api-root"),
    path("api/healthz", HealthCheckView.as_view(), name="healthz"),
    path("api/me", CurrentUserView.as_view(), name="me"),
    # Asked by nginx before it forwards a browser's spans to the collector,
    # never by the app itself. See DebugTraceVerifyView and
    # frontend/nginx.conf.template.
    path("api/debug-trace", DebugTraceVerifyView.as_view(), name="debug-trace"),
    path("api/volunteers", VolunteerListCreateView.as_view(), name="volunteers"),
    path("api/volunteers/<int:pk>", VolunteerDetailView.as_view(), name="volunteer-detail"),
    path("api/items", ItemListView.as_view(), name="items"),
    path("api/items/<int:pk>", ItemDetailView.as_view(), name="item-detail"),
    path("api/locations", LocationListView.as_view(), name="locations"),
    path("api/locations/<int:pk>", LocationDetailView.as_view(), name="location-detail"),
    path("api/categories", CategoryListView.as_view(), name="categories"),
    path("api/categories/<int:pk>", CategoryDetailView.as_view(), name="category-detail"),
    path("api/labels", LabelListView.as_view(), name="labels"),
    # Before the code route below, which would otherwise swallow it. Nothing
    # depends on that ordering -- a code is ten characters and "sheet" is five
    # -- but relying on that would be relying on the code format from here.
    path("api/labels/sheet", LabelSheetView.as_view(), name="label-sheet"),
    # By code, not by id: the code is what is printed on the sticker, and it is
    # how a label is both resolved and revoked. See LabelResolveView.
    path("api/labels/<str:code>", LabelResolveView.as_view(), name="label-resolve"),
    path(
        "api/stock/transactions",
        StockTransactionCreateView.as_view(),
        name="stock-transactions",
    ),
    path("api/schema", SpectacularAPIView.as_view(), name="schema"),
    # Split, so the page carries no inline script at all: the standard view
    # boots Swagger UI from an inline block, which no directive short of
    # 'unsafe-inline' admits. This one answers the same URL with that block as
    # a script when asked for it, which `script-src 'self'` allows -- so the
    # policy is the same everywhere and this page needs no exception. Its
    # assets come from the sidecar package rather than a CDN; see
    # SPECTACULAR_SETTINGS.
    path("api/docs", SpectacularSwaggerSplitView.as_view(url_name="schema"), name="docs"),
    path("api/schema", SpectacularAPIView.as_view(), name="schema"),
]
