"""Structure checks: the seat services must not depend on HTTP objects."""

import inspect

from django.test import SimpleTestCase

from registrations import services


class ServicesStructureTests(SimpleTestCase):
    """Proves the services take plain values and import nothing from the request layer."""

    def test_cancel_event_has_no_request_parameter(self):
        self.assertNotIn("request", inspect.signature(services.cancel_event).parameters)

    def test_apply_event_edit_has_no_request_parameter(self):
        self.assertNotIn("request", inspect.signature(services.apply_event_edit).parameters)

    def test_services_module_does_not_import_the_http_layer(self):
        source = inspect.getsource(services)
        self.assertNotIn("rest_framework.request", source)
        self.assertNotIn("rest_framework.response", source)
