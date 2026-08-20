"""The OpenAPI schema is a promise; these tests are what keep it true.

The schema at backend/openapi.yaml is committed so consumers can read it
without running the project. A committed generated file rots the moment
someone forgets to regenerate it, so drift fails the test run -- locally and in
CI identically, the same approach as the coverage threshold in
docs/decisions/0007-test-coverage.md.

If one of these fails, you changed the API. Run:

    uv run python src/manage.py spectacular --file openapi.yaml
"""

import io
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlparse

import pytest
import yaml
from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import Client
from django.urls import reverse

from inventory.api import ADMINISTRATORS_ONLY, VALIDATION_ERROR
from inventory.models import Category, Location
from inventory.tests.conftest import SCHEMA_PATH
from inventory.tests.helpers import post
from inventory.views import ENDPOINTS


def generated_schema() -> str:
    out = io.StringIO()
    call_command("spectacular", stdout=out)
    return out.getvalue()


def test_committed_schema_matches_the_code() -> None:
    assert SCHEMA_PATH.exists(), f"{SCHEMA_PATH} is missing"
    assert SCHEMA_PATH.read_text() == generated_schema(), (
        "backend/openapi.yaml is out of date with the API. Regenerate it:\n"
        "    uv run python src/manage.py spectacular --file openapi.yaml"
    )


def test_schema_declares_the_expected_openapi_version() -> None:
    """Pinned deliberately -- see docs/decisions/0010-openapi-version.md."""
    assert yaml.safe_load(SCHEMA_PATH.read_text())["openapi"] == "3.1.1"


# OpenAPI path items also carry non-operation keys such as `parameters` and
# `servers`; only these are operations.
HTTP_METHODS = frozenset({"get", "put", "post", "delete", "options", "head", "patch", "trace"})

# The two index entries that are not paths within the schema: they are the
# document itself and its rendering.
NOT_PATHS = ("schema", "docs")


def test_every_endpoint_documents_its_response(schema: Mapping[str, Any]) -> None:
    """A spec that lists paths but not payloads is not much of a spec."""
    for path, path_item in schema["paths"].items():
        for method, operation in path_item.items():
            if method not in HTTP_METHODS:
                continue
            where = f"{method.upper()} {path}"
            # Any success code, not just 200: a create answers 201 and a delete
            # 204, and 204 carries no body by definition.
            successes = {code: response for code, response in operation["responses"].items() if code.startswith("2")}
            assert successes, f"{where} documents no successful response"
            for code, response in successes.items():
                if code == "204":
                    continue
                content = response.get("content")
                assert content, f"{where} -> {code} documents no response body"
                # Every media type it offers, not application/json alone: the
                # label sheet is a printable HTML document and is the one
                # endpoint here that answers with anything else.
                for media_type, body in content.items():
                    assert "schema" in body, f"{where} -> {code} has an untyped {media_type} response"


def test_the_schema_describes_every_endpoint_the_index_advertises(schema: Mapping[str, Any]) -> None:
    """The index and its schema are generated from one dict; they must agree.

    They can still come apart: the fields are built per name, and one shared
    field instance would bind to the first name only, leaving the schema
    describing a single property while the endpoint returns all of them.
    """
    described = schema["components"]["schemas"]["ApiRoot"]
    assert set(described["properties"]) == set(ENDPOINTS)
    assert set(described["required"]) == set(ENDPOINTS)


@pytest.mark.django_db
def test_api_root_lists_the_endpoints(client: Client) -> None:
    response = client.get(reverse("api-root"))
    assert response.status_code == 200
    assert set(response.json()) == set(ENDPOINTS)


@pytest.mark.django_db
def test_every_advertised_link_is_described_in_the_schema(client: Client, schema: Mapping[str, Any]) -> None:
    """Discovery is only useful if what it points at is described.

    Asserted against the schema rather than by fetching each link, because an
    entry point reached by POST -- `/api/stock/transactions`, the endpoint this
    project exists for -- has no GET to succeed and used to be left out of the
    index for that reason alone. See ENDPOINTS in views.py.

    The two entries in NOT_PATHS are the ones this cannot ask about.
    """
    body = client.get(reverse("api-root")).json()
    for name, url in body.items():
        if name in NOT_PATHS:
            continue
        path = urlparse(url).path
        assert path in schema["paths"], f"{name} -> {path} is advertised but not described"


@pytest.mark.usefixtures("_static_files_are_not_collected")
@pytest.mark.django_db
def test_every_advertised_link_that_answers_a_get_answers_it(client: Client, schema: Mapping[str, Any]) -> None:
    """The half of the old assertion that is still true, kept.

    Logged in, because most of what the index advertises is not public. The
    index itself and the health check are; the volunteer pick-list is not.
    """
    client.force_login(User.objects.create_user(username="reader", password="not-a-real-password"))
    body = client.get(reverse("api-root")).json()
    for name, url in body.items():
        answers_a_get = name in NOT_PATHS or "get" in schema["paths"].get(urlparse(url).path, {})
        if answers_a_get:
            assert client.get(url).status_code == 200, f"{name} -> {url} does not resolve"


@pytest.mark.django_db
def test_fetching_the_index_sets_the_csrf_cookie(client: Client) -> None:
    """A single-page app never renders a Django template, so nothing else would
    ever set it and no browser could write to the API.

    The test client receives cookies even though it does not enforce CSRF, so
    this regression is catchable here in a second rather than only in the
    browser suite.
    """
    assert "csrftoken" in client.get(reverse("api-root")).cookies


@pytest.mark.django_db
def test_the_public_links_resolve_without_logging_in(client: Client) -> None:
    """Probes run before authentication exists, and a client that cannot reach
    the index cannot discover the login route either.

    `me` is public for a third reason: the volunteer app asks it what it may do
    before anybody has signed in, and never will. See CurrentUserView.
    """
    for name in ("api-root", "healthz", "me"):
        assert client.get(reverse(name)).status_code == 200


@pytest.mark.django_db
def test_schema_endpoint_serves_the_same_document(client: Client) -> None:
    served = client.get(reverse("schema")).content.decode()
    assert served == SCHEMA_PATH.read_text()


# --------------------------------------------------------------------------
# Who each operation is for, and what it can refuse with
# --------------------------------------------------------------------------


def test_the_schema_says_which_operations_an_administrator_must_make(schema: Mapping[str, Any]) -> None:
    """An endpoint's audience is part of its contract -- decision 0012.

    Read off the committed document rather than the view, because the document
    is what a client author has. Read off the description rather than the 403,
    because every endpoint here refuses an anonymous caller with a 403 too:
    the status code says "not you", and only this says who it would have been.
    """
    for path in ("/api/items", "/api/locations", "/api/categories", "/api/labels"):
        for method in ("post", "patch", "put"):
            operation = schema["paths"][path].get(method)
            if operation is None:
                continue
            assert ADMINISTRATORS_ONLY in operation.get("description", ""), (
                f"{method.upper()} {path} does not say who it is for"
            )


def test_reading_is_not_declared_as_an_administrators_operation(schema: Mapping[str, Any]) -> None:
    """The marker names the seam; on a read it would be a lie."""
    assert ADMINISTRATORS_ONLY not in schema["paths"]["/api/items"]["get"].get("description", "")


def test_a_read_still_declares_the_refusal_it_can_answer_with(schema: Mapping[str, Any]) -> None:
    """Anonymous callers get a 403 from every endpoint but three, so it is documented."""
    assert "403" in schema["paths"]["/api/items"]["get"]["responses"]
    assert "403" not in schema["paths"]["/api/healthz"]["get"]["responses"]


def test_every_endpoint_addressing_one_row_declares_its_404(schema: Mapping[str, Any]) -> None:
    """The retired-row rule above is only reachable through a 404, so clients need it."""
    for path, operations in schema["paths"].items():
        if "{" not in path:
            continue
        for method, operation in operations.items():
            # A path item may carry keys that are not operations -- `parameters`
            # and `summary` are both legal there -- and indexing one of those
            # for a response would fail as a TypeError rather than an assertion.
            if method not in HTTP_METHODS:
                continue
            assert "404" in operation["responses"], f"{method.upper()} {path} cannot say the row is not there"


# The two operations that answer 400 with something other than the field map,
# and why. The batch endpoint's body carries the position of the offending
# line, which a map keyed by field name cannot; the label sheet's is the
# printable document itself, because it renders its own refusals rather than
# answering a browser with JSON.
BESPOKE_400 = {("/api/stock/transactions", "post"): "#/components/schemas/BatchRejected"}
DOCUMENT_400 = ("/api/labels/sheet", "get")


def test_every_operation_with_a_body_declares_the_400_refusing_it_answers_with(schema: Mapping[str, Any]) -> None:
    """A serializer that names the bad field is only useful if a client can type the answer.

    Keyed on the request body rather than on the method: a write is a write
    because it submits something, and the something is what gets refused.
    """
    for path, operations in schema["paths"].items():
        for method, operation in operations.items():
            if method not in HTTP_METHODS or "requestBody" not in operation:
                continue
            where = f"{method.upper()} {path}"
            assert "400" in operation["responses"], f"{where} does not say what a refused body answers with"
            body = operation["responses"]["400"]["content"]["application/json"]["schema"]
            expected = BESPOKE_400.get((path, method), f"#/components/schemas/{VALIDATION_ERROR}")
            assert body == {"$ref": expected}, f"{where} answers 400 with an unexpected shape"


def test_an_operation_with_nothing_to_validate_claims_no_400(schema: Mapping[str, Any]) -> None:
    """A promise the operation cannot keep is worse than no promise.

    Nothing to validate means no request body *and* no filter: a filtered list
    refuses a query parameter it cannot parse, with the same field map, so it
    earns its 400 without carrying a body.

    The label sheet is the single exception among those and declares its own:
    what it refuses is a query string, and it refuses it with the document.
    """
    filtered = {urlparse(reverse(name)).path for name in ("items", "locations", "categories", "volunteers")}
    for path, operations in schema["paths"].items():
        for method, operation in operations.items():
            if method not in HTTP_METHODS or "requestBody" in operation:
                continue
            where = f"{method.upper()} {path}"
            if (path, method) == DOCUMENT_400:
                assert set(operation["responses"]["400"]["content"]) == {"text/html"}, (
                    f"{where} refuses with the document, not with JSON"
                )
                continue
            if path in filtered:
                assert "400" in operation["responses"], f"{where} can refuse a query parameter and does not say so"
                continue
            assert "400" not in operation["responses"], f"{where} claims a refusal it cannot produce"


def test_the_published_400_is_the_map_of_field_to_messages_drf_renders(schema: Mapping[str, Any]) -> None:
    """The shape is not a serializer's fields, so nothing else would catch it changing."""
    published = schema["components"]["schemas"][VALIDATION_ERROR]
    assert published["type"] == "object"
    assert published["additionalProperties"] == {"type": "array", "items": {"type": "string"}}


@pytest.mark.django_db
def test_a_refused_write_answers_with_the_shape_the_schema_publishes(editor: Client, category: Category) -> None:
    """The schema describes what DRF already does; this is what keeps that true.

    Both halves of the shape, from two rules that fail in different ways: a
    field the item serializer rejects on its own, and a location whose fields
    are each fine and whose combination is not.
    """
    refusals = [
        post(editor, "items", {"name": "Broken", "category": category.pk, "reorder_quantity": "0"}),
        post(editor, "locations", {"name": "Nobody", "kind": Location.Kind.VOLUNTEER_CUSTODY}),
    ]
    for response in refusals:
        assert response.status_code == 400, response.content
        body = response.json()
        assert isinstance(body, dict) and body
        for field, messages in body.items():
            assert isinstance(messages, list) and messages, f"{field} carries no messages"
            assert all(isinstance(message, str) for message in messages), f"{field} carries something unreadable"
    assert "reorder_quantity" in refusals[0].json()
    assert "non_field_errors" in refusals[1].json()


@pytest.mark.django_db
def test_a_filtered_list_really_refuses_a_query_parameter_it_cannot_parse(client: Client) -> None:
    """The half of the promise above that only a live request can keep.

    django-filter is configured to raise rather than to ignore, so this is a
    400 with the same field map a refused write answers with -- which is why
    the schema declares one on a list that carries no body.
    """
    response = client.get(reverse("items"), {"category": "not-a-number"})

    assert response.status_code == 400, response.content
    body = response.json()
    assert "category" in body
    assert all(isinstance(message, str) for message in body["category"])
