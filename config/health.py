"""Expose a small database-backed health check."""

from django.db import connection
from drf_spectacular.utils import extend_schema
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView


class HealthView(APIView):
    """Checks that the application can query its database."""

    authentication_classes = []
    permission_classes = [AllowAny]
    http_method_names = ["get"]

    @extend_schema(exclude=True)
    def get(self, request):
        """Return success after a trivial database query."""
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        return Response({"status": "ok"})
