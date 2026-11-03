"""Shared page-number pagination responses."""

from typing import Any

from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response


def paginated_response(
    request: Any,
    queryset: Any,
    view: Any,
    serializer_class: Any,
) -> Response:
    """Return a page using the configured pagination response shape."""
    paginator = PageNumberPagination()
    page: Any = paginator.paginate_queryset(queryset, request, view=view)
    return paginator.get_paginated_response(serializer_class(page, many=True).data)
