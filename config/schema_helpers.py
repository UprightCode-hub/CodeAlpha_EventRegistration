"""Shared OpenAPI schema helpers."""

from typing import Any

from drf_spectacular.utils import OpenApiParameter, OpenApiTypes, inline_serializer
from rest_framework import serializers

BEARER_AUTH: list[Any] = [{"BearerAuth": []}]


def page_schema(name: str, item_serializer: Any) -> Any:
    """Describe a page envelope around a response item serializer."""
    return inline_serializer(
        name=name,
        fields={
            "count": serializers.IntegerField(),
            "next": serializers.URLField(allow_null=True),
            "previous": serializers.URLField(allow_null=True),
            "results": item_serializer(many=True),
        },
    )


def page_parameter() -> OpenApiParameter:
    """Describe the page query parameter."""
    return OpenApiParameter("page", OpenApiTypes.INT, OpenApiParameter.QUERY)


def idempotency_key_parameter() -> OpenApiParameter:
    """Describe the optional idempotency key header."""
    return OpenApiParameter(
        "Idempotency-Key",
        OpenApiTypes.STR,
        OpenApiParameter.HEADER,
        required=False,
        description="Optional key for safely retrying this create request.",
    )
