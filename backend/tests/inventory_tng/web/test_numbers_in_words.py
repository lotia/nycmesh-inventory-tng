"""A request's numbers in words follow its decimal separator, in an async
route and a sync one run in a thread, and never outlast the request
(web/numbers_in_words.py; Ali, 2026-11-23, inventory-tng-4mc3)."""

from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from fastapi.testclient import TestClient

from inventory_tng.core.phrases import number
from inventory_tng.web.numbers_in_words import NumbersInWords

VALUE = Decimal("1500.5")


def app() -> FastAPI:
    made = FastAPI()
    made.add_middleware(NumbersInWords)

    @made.get("/async", response_class=PlainTextResponse)
    async def in_async() -> str:
        return number(VALUE)

    @made.get("/sync", response_class=PlainTextResponse)
    def in_sync() -> str:
        return number(VALUE)

    return made


@pytest.mark.parametrize("path", ["/async", "/sync"])
@pytest.mark.parametrize(
    ("language", "written"), [("de-DE", "1500,5"), ("en-US", "1500.5")]
)
def test_a_request_writes_numbers_with_its_separator(
    path: str, language: str, written: str
) -> None:
    with TestClient(app()) as client:
        reply = client.get(path, headers={"Accept-Language": language})
    assert reply.text == written
    assert number(VALUE) == "1500.5"
