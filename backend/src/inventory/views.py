from django.db import connection
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView


class HealthCheckView(APIView):
    """Liveness and readiness probe used by Kubernetes.

    Issues a trivial query so the check fails when the database is
    unreachable, rather than reporting healthy while unable to serve.
    See docs/deployment.md.
    """

    # Deliberately public: probes run before authentication exists.
    permission_classes = [AllowAny]

    def get(self, request: Request) -> Response:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        return Response({"status": "ok"})
