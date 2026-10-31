"""Run create requests once when clients retry with an idempotency key."""

import hashlib
import json
import re
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response

from config.exceptions import error_response
from registrations.models import IdempotencyRecord

KEY_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")


def run_idempotent_create(request, endpoint, handler):
    """Run or replay a create response for a scoped idempotency key."""
    key = request.headers.get("Idempotency-Key")
    if key is None:
        return handler()
    if KEY_PATTERN.fullmatch(key) is None:
        return error_response(
            "validation_error",
            {"Idempotency-Key": ["Use 1 to 64 letters, digits, hyphens, or underscores."]},
        )

    raw_body = request._request.body
    request_hash = hashlib.sha256(raw_body).hexdigest()
    expires_before = timezone.now() - timedelta(hours=24)
    records = IdempotencyRecord.objects.filter(
        user=request.user, endpoint=endpoint, key=key
    )
    with transaction.atomic():
        records.filter(created_at__lt=expires_before).delete()
        try:
            with transaction.atomic():
                record = IdempotencyRecord.objects.create(
                    user=request.user,
                    endpoint=endpoint,
                    key=key,
                    request_hash=request_hash,
                    status_code=201,
                    response_body={},
                )
        except IntegrityError:
            # The unique insert waits for a concurrent request before this read.
            record = records.first()
            if record is None:
                raise
            if record.request_hash != request_hash:
                return error_response("idempotency_key_reused")
            return Response(
                record.response_body,
                status=record.status_code,
                headers={"Idempotent-Replayed": "true"},
            )

        response = handler()
        if response.status_code == 201:
            rendered_body = JSONRenderer().render(response.data)
            record.response_body = json.loads(rendered_body.decode("utf-8"))
            record.status_code = response.status_code
            record.save(update_fields=("response_body", "status_code"))
        else:
            transaction.set_rollback(True)
        return response
