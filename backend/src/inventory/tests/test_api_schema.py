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
from pathlib import Path

import pytest
import yaml
from django.conf import settings
from django.core.management import call_command
from django.test import Client
from django.urls import reverse

from inventory.views import ENDPOINTS

SCHEMA_PATH = Path(settings.BASE_DIR).parent / "openapi.yaml"


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


def test_every_endpoint_documents_its_response() -> None:
    """A spec that lists paths but not payloads is not much of a spec."""
    schema = yaml.safe_load(SCHEMA_PATH.read_text())
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
                assert "schema" in content["application/json"], f"{where} -> {code} has an untyped response"


@pytest.mark.django_db
def test_api_root_lists_the_endpoints(client: Client) -> None:
    response = client.get(reverse("api-root"))
    assert response.status_code == 200
    assert set(response.json()) == set(ENDPOINTS)


@pytest.mark.django_db
def test_api_root_links_actually_resolve(client: Client) -> None:
    """Discovery is only useful if the links work."""
    body = client.get(reverse("api-root")).json()
    for name, url in body.items():
        assert client.get(url).status_code == 200, f"{name} -> {url} does not resolve"


@pytest.mark.django_db
def test_schema_endpoint_serves_the_same_document(client: Client) -> None:
    served = client.get(reverse("schema")).content.decode()
    assert served == SCHEMA_PATH.read_text()
