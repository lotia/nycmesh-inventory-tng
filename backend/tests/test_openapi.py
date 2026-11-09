import json
from pathlib import Path
from typing import Any, get_args

import pytest

from inventory_tng import devices, openapi


@pytest.fixture(scope="module")
def schema() -> dict[str, Any]:
    """Built once: each build makes a whole app."""
    return openapi.schema()


def test_the_committed_schema_is_current(schema: dict[str, Any]) -> None:
    """The frontend's types come from this file, so it must say what the code
    does. Compared parsed: Biome decides its layout."""
    assert json.loads(openapi.SCHEMA.read_text(encoding="utf-8")) == schema, (
        "Run `uv run python -m inventory_tng.openapi`, then `npm run api` in frontend/"
    )


def test_writes_the_schema(tmp_path: Path, schema: dict[str, Any]) -> None:
    openapi.main(tmp_path / "openapi.json")
    assert json.loads((tmp_path / "openapi.json").read_text()) == schema


def test_every_route_declares_the_503(schema: dict[str, Any]) -> None:
    for path, operations in schema["paths"].items():
        for method, operation in operations.items():
            reply = operation["responses"]["503"]["content"]["application/json"]
            assert reply["schema"] == {
                "$ref": "#/components/schemas/UnavailableReply"
            }, f"{method} {path}"


def test_refusals_name_their_reasons(schema: dict[str, Any]) -> None:
    replies = schema["components"]["schemas"]

    def reasons(name: str) -> set[str]:
        reason = replies[name]["properties"]["reason"]
        return set(reason["enum"]) if "enum" in reason else {reason["const"]}

    assert reasons("CodeRefusedReply") == set(get_args(devices.BadCode))
    assert reasons("RateLimitedReply") == {"rate_limited"}
    assert reasons("DeviceRefusedReply") == set(get_args(devices.Refusal))


def test_routes_that_need_a_registered_device_say_so(schema: dict[str, Any]) -> None:
    """The device cookie is declared as an API key (devices.device_cookie),
    on exactly the routes that refuse an unregistered device (403), so
    /api/docs and other clients see which need it."""
    scheme = schema["components"]["securitySchemes"]["device"]
    assert (scheme["type"], scheme["in"], scheme["name"]) == (
        "apiKey",
        "cookie",
        devices.COOKIE,
    )
    needing, refusing = set(), set()
    for path, operations in schema["paths"].items():
        for method, operation in operations.items():
            if {"device": []} in operation.get("security", []):
                needing.add((method, path))
            if "403" in operation["responses"]:
                refusing.add((method, path))
    assert needing == refusing
    assert ("post", "/api/transactions") in needing
