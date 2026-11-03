"""Tests for the API schema and the Swagger page."""

import json
import re

from django.conf import settings
from django.test import TestCase
from rest_framework.test import APIClient

EXPECTED = {
    ("/api/auth/register/", "post"): "201",
    ("/api/auth/login/", "post"): "200",
    ("/api/auth/refresh/", "post"): "200",
    ("/api/auth/me/", "get"): "200",
    ("/api/events/", "get"): "200",
    ("/api/events/", "post"): "201",
    ("/api/events/{id}/", "get"): "200",
    ("/api/events/{id}/", "patch"): "200",
    ("/api/events/{id}/cancel/", "post"): "200",
    ("/api/events/{id}/registrations/", "get"): "200",
    ("/api/my/events/", "get"): "200",
    ("/api/registrations/", "get"): "200",
    ("/api/registrations/", "post"): "201",
    ("/api/registrations/{id}/", "get"): "200",
    ("/api/registrations/{id}/cancel/", "post"): "200",
    ("/api/notifications/", "get"): "200",
}

METHODS = ("get", "post", "put", "patch", "delete")

EVENT_BODY = {
    "title",
    "description",
    "category",
    "event_type",
    "venue_name",
    "address",
    "city",
    "online_link",
    "start_at",
    "end_at",
    "registration_deadline",
    "capacity",
    "contact_email",
    "contact_phone",
}
EVENT_READ = EVENT_BODY | {
    "id",
    "registered_count",
    "seats_left",
    "waitlist_count",
    "organizer_username",
    "status",
    "cancel_reason",
    "created_at",
}
REGISTRATION_READ = {
    "id",
    "event",
    "event_title",
    "event_start_at",
    "full_name",
    "phone",
    "note",
    "status",
    "waitlist_position",
    "created_at",
    "updated_at",
}
ORGANIZER_READ = REGISTRATION_READ | {"attendee_username", "attendee_email"}
NOTIFICATION_READ = {"id", "kind", "message", "event", "event_title", "registration", "created_at"}
USER_READ = {"id", "username", "email", "role"}

REQUEST_FIELDS = {
    ("/api/auth/register/", "post"): {"username", "email", "password", "role"},
    ("/api/auth/login/", "post"): {"username", "password"},
    ("/api/auth/refresh/", "post"): {"refresh"},
    ("/api/events/", "post"): EVENT_BODY,
    ("/api/events/{id}/", "patch"): EVENT_BODY,
    ("/api/events/{id}/cancel/", "post"): {"reason"},
    ("/api/registrations/", "post"): {"event", "full_name", "phone", "note"},
}

RESPONSE_FIELDS = {
    ("/api/auth/register/", "post"): USER_READ,
    ("/api/auth/login/", "post"): {"access", "refresh"},
    ("/api/auth/refresh/", "post"): {"access"},
    ("/api/auth/me/", "get"): USER_READ,
    ("/api/events/", "post"): EVENT_READ,
    ("/api/events/{id}/", "get"): EVENT_READ,
    ("/api/events/{id}/", "patch"): EVENT_READ,
    ("/api/events/{id}/cancel/", "post"): EVENT_READ,
    ("/api/registrations/", "post"): REGISTRATION_READ,
    ("/api/registrations/{id}/", "get"): REGISTRATION_READ,
    ("/api/registrations/{id}/cancel/", "post"): REGISTRATION_READ,
}

LIST_ITEM_FIELDS = {
    ("/api/events/", "get"): EVENT_READ,
    ("/api/my/events/", "get"): EVENT_READ,
    ("/api/registrations/", "get"): REGISTRATION_READ,
    ("/api/events/{id}/registrations/", "get"): ORGANIZER_READ,
    ("/api/notifications/", "get"): NOTIFICATION_READ,
}

QUERY_PARAMETERS = {
    ("/api/events/", "get"): {
        "page",
        "q",
        "city",
        "category",
        "event_type",
        "status",
        "starts_after",
        "starts_before",
    },
    ("/api/my/events/", "get"): {"page"},
    ("/api/registrations/", "get"): {"page", "status"},
    ("/api/events/{id}/registrations/", "get"): {"page"},
    ("/api/notifications/", "get"): {"page"},
}


def normal_path(path):
    """Gives every path parameter the same name so the tests do not depend on it."""
    return re.sub(r"\{[^}/]+\}", "{id}", path)


class SchemaTestBase(TestCase):
    """Loads the schema once and offers helpers that read it."""

    @classmethod
    def setUpTestData(cls):
        response = APIClient().get("/api/schema/", {"format": "json"})
        if response.status_code != 200:
            raise AssertionError(
                f"The schema did not load: {response.status_code} {response.content[:300]!r}"
            )
        cls.schema = json.loads(response.content)
        cls.paths = {normal_path(path): item for path, item in cls.schema.get("paths", {}).items()}

    def operation(self, path, method):
        self.assertIn(path, self.paths, f"{path} is not in the schema")
        self.assertIn(method, self.paths[path], f"{method} {path} is not in the schema")
        return self.paths[path][method]

    def resolve(self, node):
        if "$ref" in node:
            name = node["$ref"].split("/")[-1]
            return self.resolve(self.schema["components"]["schemas"][name])
        return node

    def properties_of(self, node):
        node = self.resolve(node)
        found = dict(node.get("properties", {}))
        for part in node.get("allOf", []):
            found.update(self.properties_of(part))
        return found

    def body_schema(self, content):
        media = content.get("application/json") or next(iter(content.values()))
        return media["schema"]


class SchemaRouteTests(TestCase):
    """Checks the schema and docs routes and that the other routes still work."""

    def test_the_schema_is_public_in_both_formats(self):
        client = APIClient()
        self.assertEqual(client.get("/api/schema/").status_code, 200)
        response = client.get("/api/schema/", {"format": "json"})
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertTrue(data["openapi"].startswith("3."))
        self.assertEqual(data["info"]["title"], "Event Registration API")
        self.assertTrue(data["info"]["version"])

    def test_the_swagger_page_is_public_and_points_at_the_schema(self):
        response = APIClient().get("/api/docs/")
        self.assertEqual(response.status_code, 200)
        content = response.content.decode().lower()
        self.assertIn("swagger", content)
        self.assertIn("/api/schema/", content)

    def test_unknown_urls_still_return_the_json_not_found(self):
        for path in ("/api/does-not-exist/", "/api/schemas/", "/api/docs/missing/"):
            with self.subTest(path=path):
                response = APIClient().get(path)
                self.assertEqual(response.status_code, 404)
                self.assertEqual(response.json()["error"]["code"], "not_found")

    def test_the_admin_without_a_slash_still_redirects(self):
        response = APIClient().get("/" + settings.ADMIN_URL.rstrip("/"))
        self.assertIn(response.status_code, (301, 302))
        self.assertEqual(response["Location"], "/" + settings.ADMIN_URL)


class SchemaContentTests(SchemaTestBase):
    """Checks that the schema lists and describes every operation."""

    def test_the_schema_lists_exactly_the_api_operations(self):
        found = {
            (path, method)
            for path, item in self.paths.items()
            if path.startswith("/api/")
            for method in METHODS
            if method in item
        }
        self.assertEqual(found, set(EXPECTED))

    def test_the_schema_does_not_list_itself_or_the_admin(self):
        for path in self.paths:
            self.assertFalse(path.startswith(("/admin", "/" + settings.ADMIN_URL)), path)
            self.assertFalse(path.startswith("/api/schema"), path)
            self.assertFalse(path.startswith("/api/docs"), path)

    def test_every_operation_has_a_summary(self):
        for path, method in EXPECTED:
            with self.subTest(operation=f"{method} {path}"):
                summary = self.operation(path, method).get("summary")
                self.assertTrue(isinstance(summary, str) and summary.strip())

    def test_every_operation_declares_its_success_code(self):
        for (path, method), code in EXPECTED.items():
            with self.subTest(operation=f"{method} {path}"):
                responses = self.operation(path, method)["responses"]
                self.assertIn(code, responses)
                wrong = "200" if code == "201" else "201"
                self.assertNotIn(wrong, responses)

    def test_request_bodies_list_their_fields(self):
        for (path, method), fields in REQUEST_FIELDS.items():
            with self.subTest(operation=f"{method} {path}"):
                operation = self.operation(path, method)
                self.assertIn("requestBody", operation)
                found = self.properties_of(self.body_schema(operation["requestBody"]["content"]))
                self.assertTrue(fields <= set(found), f"missing {fields - set(found)}")

    def test_single_answers_list_their_fields(self):
        for (path, method), fields in RESPONSE_FIELDS.items():
            with self.subTest(operation=f"{method} {path}"):
                operation = self.operation(path, method)
                code = EXPECTED[(path, method)]
                content = operation["responses"][code]["content"]
                found = self.properties_of(self.body_schema(content))
                self.assertTrue(fields <= set(found), f"missing {fields - set(found)}")

    def test_lists_document_the_page_envelope_and_their_items(self):
        for (path, method), fields in LIST_ITEM_FIELDS.items():
            with self.subTest(operation=f"{method} {path}"):
                content = self.operation(path, method)["responses"]["200"]["content"]
                envelope = self.properties_of(self.body_schema(content))
                self.assertTrue({"count", "next", "previous", "results"} <= set(envelope))
                results = self.resolve(envelope["results"])
                self.assertEqual(results.get("type"), "array")
                items = self.properties_of(results["items"])
                self.assertTrue(fields <= set(items), f"missing {fields - set(items)}")

    def test_lists_document_their_query_parameters(self):
        for (path, method), names in QUERY_PARAMETERS.items():
            with self.subTest(operation=f"{method} {path}"):
                parameters = self.operation(path, method).get("parameters", [])
                found = {item["name"] for item in parameters if item.get("in") == "query"}
                self.assertTrue(names <= found, f"missing {names - found}")

    def test_a_bearer_token_scheme_is_declared_and_used_by_protected_operations(self):
        schemes = self.schema.get("components", {}).get("securitySchemes", {})
        bearer = {
            name
            for name, scheme in schemes.items()
            if scheme.get("type") == "http" and scheme.get("scheme", "").lower() == "bearer"
        }
        self.assertTrue(bearer)
        protected = (
            ("/api/auth/me/", "get"),
            ("/api/events/", "post"),
            ("/api/events/{id}/cancel/", "post"),
            ("/api/registrations/", "post"),
            ("/api/notifications/", "get"),
        )
        for path, method in protected:
            with self.subTest(operation=f"{method} {path}"):
                security = self.operation(path, method).get("security", [])
                used = {name for item in security for name in item}
                self.assertTrue(used & bearer)
