"""Root URL configuration. See docs/architecture.md for the API layout."""

from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerSplitView

from inventory.views import (
    ApiRootView,
    CategoryDetailView,
    CategoryListView,
    ClientFailureView,
    CurrentUserView,
    DebugTraceVerifyView,
    DeviceEnrolmentView,
    HealthCheckView,
    ItemDetailView,
    ItemListView,
    LabelListView,
    LabelResolveView,
    LabelSheetView,
    LivenessCheckView,
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
    # Asked by the kubelet and by nobody else, which is why it is not in
    # ENDPOINTS beside the readiness check above: a client discovering this
    # API has no use for a question only a kubelet acts on. See
    # LivenessCheckView for what separates the two, and the chart's
    # backend-deployment.yaml for which probe asks which.
    path("api/livez", LivenessCheckView.as_view(), name="livez"),
    path("api/me", CurrentUserView.as_view(), name="me"),
    # Where a browser asks to be told apart from the others. Credential-free
    # because a device has nothing to present until it has enrolled; what
    # stands in for that, and what the credential is and is not, is
    # DeviceEnrolmentView and inventory_tng/devices.py.
    path("api/devices", DeviceEnrolmentView.as_view(), name="devices"),
    # Where a browser reports what it could not handle. Credential-free like
    # the ones beside it, and argued in decision 0012.
    path("api/client-failures", ClientFailureView.as_view(), name="client-failures"),
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
    # BEFORE the code route below, and the ordering is REQUIRED rather than
    # tidiness. StringConverter matches [^/]+, so "sheet" is a code as far as
    # the router can tell and the route below would answer it: swap the two and
    # ten tests in test_label_printing.py fail, /api/labels/sheet resolving by
    # route='api/labels/<str:code>' to a 403.
    #
    # What the ordering costs is that this literal hides any code equal to it,
    # and today there is none -- a code is CODE_LENGTH characters and "sheet" is
    # five. That half DOES rest on the code format, which is why it is written
    # down rather than assumed: shorten CODE_LENGTH to five and this line would
    # start swallowing a label instead of the other way round.
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
]
