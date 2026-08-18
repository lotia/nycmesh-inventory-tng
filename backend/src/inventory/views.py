from django.db import connection
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.reverse import reverse
from rest_framework.views import APIView

# The one place the endpoint index is declared. The response body, the schema
# and the discovery test are all derived from it, so an endpoint cannot be
# advertised without being described, or described without being advertised.
ENDPOINTS = {
    "health": "healthz",
    "schema": "schema",
    "docs": "docs",
}


class ApiRootView(APIView):
    """The list of endpoints this API offers.

    Exists so the API is discoverable without reading the source or knowing the
    URL layout in advance: fetch this, follow the links.
    """

    # Deliberately public: an index of endpoint names is not sensitive, and a
    # client that cannot discover the login route cannot authenticate.
    permission_classes = [AllowAny]

    @extend_schema(
        summary="List the available endpoints",
        responses=inline_serializer(
            name="ApiRoot",
            fields=dict.fromkeys(ENDPOINTS, serializers.URLField()),
        ),
    )
    def get(self, request: Request) -> Response:
        return Response({key: reverse(name, request=request) for key, name in ENDPOINTS.items()})


class HealthCheckView(APIView):
    """Liveness and readiness probe used by Kubernetes.

    Issues a trivial query so the check fails when the database is
    unreachable, rather than reporting healthy while unable to serve.
    See docs/deployment.md.
    """

    # Deliberately public: probes run before authentication exists.
    permission_classes = [AllowAny]

    @extend_schema(
        summary="Liveness and readiness probe",
        responses=inline_serializer(name="HealthCheck", fields={"status": serializers.CharField()}),
    )
    def get(self, request: Request) -> Response:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        return Response({"status": "ok"})
