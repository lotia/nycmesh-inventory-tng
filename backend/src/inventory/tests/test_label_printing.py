"""Minting a label's code, and printing the sticker it goes on.

Decision 0011 section 3 says the generator's output is asserted by tests rather
than described in prose, "because a rule that lives only in prose drifts the
first time somebody fits a label to a smaller sticker, and the failure
resurfaces months later as labels that will not scan". So the symbols this
produces are decoded here by a different implementation from the one that drew
them -- ``zxing-cpp`` reading what ``segno`` wrote -- and the geometry is read
back out of the SVG rather than taken on trust.
"""

import datetime
import re
from typing import Any

import pytest
import zxingcpp
from django.core.exceptions import ImproperlyConfigured
from django.db import IntegrityError
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from rest_framework.exceptions import Throttled

from inventory.labels import (
    ERROR_CORRECTION,
    LABEL_WIDTH_MM,
    MINIMUM_MODULE_MM,
    QUIET_ZONE_MODULES,
    deep_link,
    svg_for,
    symbol_for,
)
from inventory.models import CODE_ALPHABET, CODE_LENGTH, MINT_ATTEMPTS, Item, Label, Location
from inventory.tests.helpers import patch, post
from inventory.views import MAX_SHEET_LABELS, LabelSheetView

# One well-formed code, used wherever the test is about something other than
# minting. It is the example in decision 0011.
CODE = "7QK3M2XV9A"

# What the deployment prints on its stickers while these tests run. Pinned so
# that a change to the default in settings cannot quietly change the version of
# the symbol every assertion below is about.
HOST = "https://inventory.nycmesh.net"

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _printed_host(settings: Any) -> None:
    """Pin the host for every test here.

    A developer with LABEL_BASE_URL in their .env would otherwise be printing a
    different symbol from the one every assertion below is about, and would
    find out as a failure that says nothing about why.
    """
    settings.LABEL_BASE_URL = HOST


# --------------------------------------------------------------------------
# Reading a drawn symbol back
# --------------------------------------------------------------------------

# One run of dark modules, as svg_for draws them.
RUN = re.compile(r"M(\d+) (\d+)h(\d+)v1h-\d+z")

SVG_SIZE = re.compile(r'width="([\d.]+)mm" height="([\d.]+)mm" viewBox="0 0 (\d+) (\d+)"')


def matrix_from(svg: str) -> list[list[int]]:
    """The module grid the SVG draws, quiet zone included.

    Read out of the document rather than off the encoder, so what is decoded
    below is what would be printed.
    """
    size = SVG_SIZE.search(svg)
    assert size is not None, svg[:200]
    across = int(size.group(3))
    grid = [[0] * across for _ in range(across)]
    for x, y, length in ((int(a), int(b), int(c)) for a, b, c in RUN.findall(svg)):
        for offset in range(length):
            grid[y][x + offset] = 1
    return grid


def decode(svg: str) -> Any:
    """What a scanner makes of that grid.

    Rasterised at four pixels a module -- a scanner needs more than one sample
    per module, and this is a decode test rather than a resolution one.
    """
    grid = matrix_from(svg)
    scale = 4
    width = len(grid) * scale
    pixels = bytearray(b"\xff" * (width * width))
    for y, row in enumerate(grid):
        for x, dark in enumerate(row):
            if not dark:
                continue
            for down in range(scale):
                for across in range(scale):
                    pixels[(y * scale + down) * width + x * scale + across] = 0
    found = zxingcpp.read_barcodes(memoryview(bytes(pixels)).cast("B", (width, width)), is_pure=True)
    assert len(found) == 1, f"{len(found)} symbols decoded from one label"
    return found[0]


# --------------------------------------------------------------------------
# Minting
# --------------------------------------------------------------------------


def test_a_minted_code_is_ten_characters_of_the_alphabet() -> None:
    """Both halves of the check constraint, from the other side."""
    for _ in range(200):
        code = Label.mint_code()
        assert len(code) == CODE_LENGTH
        assert set(code) <= set(CODE_ALPHABET)


def test_minted_codes_are_not_all_the_same_code() -> None:
    """A minter that always drew the same character would pass the test above."""
    assert len({Label.mint_code() for _ in range(50)}) > 1


def test_the_alphabet_excludes_the_letters_the_resolver_folds_away() -> None:
    """Why Crockford, and not Base32 as RFC 4648 spells it.

    A code containing one of these would fold to a string matching nothing and
    be unresolvable for the life of the object carrying it. U is out for
    Crockford's own reason: a token read aloud across a room.
    """
    assert not set("ILOU") & set(CODE_ALPHABET)


def test_minting_draws_again_when_a_code_is_already_taken(item: Item, monkeypatch: pytest.MonkeyPatch) -> None:
    taken = Label.objects.create(code=CODE, item=item).code
    drawn = iter([taken, "ZZZZZZZZZZ"])
    monkeypatch.setattr(Label, "mint_code", classmethod(lambda cls: next(drawn)))

    assert Label.mint_unique_code() == "ZZZZZZZZZZ"


def test_minting_gives_up_rather_than_looping(item: Item, monkeypatch: pytest.MonkeyPatch) -> None:
    """Unreachable at fifty bits, and an error naming the cause if it ever is.

    The alternative is an IntegrityError from the unique index, which says
    nothing about why the code space ran out.
    """
    Label.objects.create(code=CODE, item=item)
    monkeypatch.setattr(Label, "mint_code", classmethod(lambda cls: CODE))

    with pytest.raises(RuntimeError, match=str(MINT_ATTEMPTS)):
        Label.mint_unique_code()


@pytest.mark.parametrize(
    "code",
    [
        "7QK3M2XV9",  # nine characters
        "7QK3M2XV9AB",  # eleven
        "",
        "7QK3M2XVIA",  # I, which the resolver folds to 1
        "7QK3M2XVLA",  # L, likewise
        "7QK3M2XVOA",  # O, folded to 0
        "7QK3M2XVUA",  # U, which Crockford leaves out
        "7qk3m2xv9a",  # lowercase, which the resolver would uppercase away
        "7QK3-M2XV9",  # punctuation
    ],
)
def test_the_database_refuses_a_code_outside_the_format(item: Item, code: str) -> None:
    """The minter is one write path; the admin, fixtures and the importer are others.

    Written through ``update``, which is the one way to reach the column
    without ``Label.save`` normalising on the way past -- the point being that
    the constraint holds without it.
    """
    label = Label.objects.create(code=CODE, item=item)
    with pytest.raises(IntegrityError):
        Label.objects.filter(pk=label.pk).update(code=code)


def test_printing_a_label_mints_its_code(editor: Client, item: Item) -> None:
    response = post(editor, "labels", {"item": item.pk, "quantity": "100"})

    assert response.status_code == 201, response.content
    minted = response.json()["code"]
    assert len(minted) == CODE_LENGTH
    assert set(minted) <= set(CODE_ALPHABET)
    assert Label.objects.get().code == minted


def test_two_labels_printed_together_do_not_share_a_code(editor: Client, item: Item) -> None:
    first = post(editor, "labels", {"item": item.pk}).json()["code"]
    second = post(editor, "labels", {"item": item.pk}).json()["code"]
    assert first != second


def test_a_client_may_not_choose_the_code(editor: Client, item: Item) -> None:
    """Refused rather than ignored: a client that chose one would print it."""
    response = post(editor, "labels", {"code": CODE, "item": item.pk})

    assert response.status_code == 400, response.content
    assert "code" in response.json()
    assert not Label.objects.exists()


def test_a_minted_code_is_what_the_scan_resolves(editor: Client, item: Item) -> None:
    """The whole round trip: mint, print, scan."""
    minted = post(editor, "labels", {"item": item.pk, "quantity": "100"}).json()["code"]

    resolved = editor.get(reverse("label-resolve", args=[minted]))

    assert resolved.status_code == 200
    assert resolved.json()["item"] == item.pk


def test_revoking_and_reprinting_leaves_the_item_alone(editor: Client, item: Item) -> None:
    """The acceptance criterion: revoke and reprint without touching identity."""
    faded = post(editor, "labels", {"item": item.pk, "quantity": "100"}).json()["code"]
    assert patch(editor, "label-resolve", {"revoked": True}, faded).status_code == 200

    fresh = post(editor, "labels", {"item": item.pk, "quantity": "100"}).json()["code"]

    assert fresh != faded
    assert Label.objects.get(code=fresh).item == item
    assert Label.objects.get(code=faded).revoked_at is not None
    assert [entry["code"] for entry in editor.get(reverse("labels")).json()] == [fresh]


# --------------------------------------------------------------------------
# What the symbol carries
# --------------------------------------------------------------------------


def test_the_payload_is_the_deep_link_in_upper_case() -> None:
    """The path shape is inventory-tng-ea6's deep link; the case is the encoder's."""
    assert deep_link(CODE) == "HTTPS://INVENTORY.NYCMESH.NET/S/7QK3M2XV9A"


def test_the_host_comes_from_the_environment(settings: Any) -> None:
    """Printed on every sticker, so it is a setting and not a literal."""
    settings.LABEL_BASE_URL = "https://stock.example.org/"
    assert deep_link(CODE) == "HTTPS://STOCK.EXAMPLE.ORG/S/7QK3M2XV9A"


def test_the_symbol_is_encoded_in_alphanumeric_mode() -> None:
    """Two characters per 11 bits rather than 8 bits each -- decision 0011."""
    assert symbol_for(CODE).mode == "alphanumeric"


def test_a_host_the_alphanumeric_mode_cannot_carry_is_refused(settings: Any) -> None:
    """Loudly, at the moment of printing, rather than as a symbol a version larger."""
    settings.LABEL_BASE_URL = "https://inventory_tng.example.net"
    with pytest.raises(ImproperlyConfigured, match="alphanumeric"):
        symbol_for(CODE)


def test_the_symbol_decodes_back_to_what_it_encoded() -> None:
    """A different implementation reading what this one drew."""
    assert decode(svg_for(CODE)).text == deep_link(CODE)


def test_the_symbol_carries_error_correction_level_q() -> None:
    """Not the L or M most generators default to. ISO/IEC 18004 clause 5.1 f)
    puts level Q at 25 % of codewords recoverable."""
    assert decode(svg_for(CODE)).ec_level == ERROR_CORRECTION


def test_the_symbol_is_the_version_the_payload_needs_and_no_larger() -> None:
    """Version 3, which is 29 modules square.

    Derived from the symbol rather than asserted as a magic number: ISO/IEC
    18004 clause 5.1 d) 2) puts version 1 at 21 modules and each version after
    it at four more per side.
    """
    symbol = symbol_for(CODE)
    assert symbol.version == 3
    assert len(symbol.matrix) == 21 + 4 * (symbol.version - 1)


# --------------------------------------------------------------------------
# What the printed label measures
# --------------------------------------------------------------------------


def test_the_symbol_is_drawn_in_millimetres() -> None:
    """A length in device pixels means whatever the print pipeline decides."""
    size = SVG_SIZE.search(svg_for(CODE))
    assert size is not None
    assert float(size.group(1)) == LABEL_WIDTH_MM
    assert float(size.group(2)) == LABEL_WIDTH_MM


def test_the_modules_are_no_smaller_than_the_floor() -> None:
    """The assertion decision 0011 asks for: module count against physical size.

    Fitting the label to a smaller sticker, or lengthening the host until the
    symbol grows a version, is what this catches.
    """
    size = SVG_SIZE.search(svg_for(CODE))
    assert size is not None
    across = int(size.group(3))
    assert LABEL_WIDTH_MM / across >= MINIMUM_MODULE_MM


def test_the_quiet_zone_is_four_modules_on_every_side() -> None:
    """ISO/IEC 18004 clause 5.3.8, drawn into the SVG rather than left to the paper."""
    grid = matrix_from(svg_for(CODE))
    edges = (
        grid[:QUIET_ZONE_MODULES],
        grid[-QUIET_ZONE_MODULES:],
        [row[:QUIET_ZONE_MODULES] for row in grid],
        [row[-QUIET_ZONE_MODULES:] for row in grid],
    )
    assert not any(any(row) for edge in edges for row in edge)
    # And the symbol starts immediately after it, so the zone is four modules
    # and not merely at least four.
    assert any(grid[QUIET_ZONE_MODULES][QUIET_ZONE_MODULES:])


def test_a_label_too_small_for_its_symbol_is_refused(settings: Any) -> None:
    """The guard rail. A longer host is a larger symbol on the same sticker."""
    settings.LABEL_BASE_URL = f"https://{'inventory-nycmesh-' * 6}example.net"
    with pytest.raises(ImproperlyConfigured, match="mm per module"):
        svg_for(CODE)


# --------------------------------------------------------------------------
# The printable sheet
# --------------------------------------------------------------------------


def printed_at(label: Label, when: datetime.datetime) -> Label:
    """Backdate a label, which ``printed_at`` will not take on creation."""
    Label.objects.filter(pk=label.pk).update(printed_at=when)
    label.refresh_from_db()
    return label


def test_the_sheet_is_an_html_document(editor: Client, item: Item) -> None:
    Label.objects.create(code=CODE, item=item)

    response = editor.get(reverse("label-sheet"), {"code": CODE})

    assert response.status_code == 200, response.content
    assert response["Content-Type"].startswith("text/html")
    assert response.content.decode().lstrip().startswith("<!doctype html>")


def test_the_sheet_carries_the_code_as_readable_text(editor: Client, item: Item) -> None:
    """A dead QR is still a working label if the code can be read and typed."""
    Label.objects.create(code=CODE, item=item)

    body = editor.get(reverse("label-sheet"), {"code": CODE}).content.decode()

    assert f'<p class="code">{CODE}</p>' in body


def test_the_sheet_carries_the_date_the_label_was_printed(editor: Client, item: Item) -> None:
    """So an aged batch can be found and replaced as a set."""
    label = Label.objects.create(code=CODE, item=item)
    printed_at(label, datetime.datetime(2026, 3, 4, 17, 0, tzinfo=datetime.UTC))

    body = editor.get(reverse("label-sheet"), {"code": CODE}).content.decode()

    assert '<p class="printed">2026-03-04</p>' in body


def test_the_sheets_symbols_decode(editor: Client, item: Item, warehouse: Location) -> None:
    """Everything on the page, not just one drawn in isolation."""
    Label.objects.create(code=CODE, item=item, quantity=100)
    Label.objects.create(code="ZZZZZZZZZZ", location=warehouse)

    body = editor.get(reverse("label-sheet"), {"code": f"{CODE},ZZZZZZZZZZ"}).content.decode()

    decoded = {decode(svg).text for svg in re.findall(r"<svg .*?</svg>", body, re.DOTALL)}
    assert decoded == {deep_link(CODE), deep_link("ZZZZZZZZZZ")}


def test_the_sheet_prints_only_the_codes_it_was_asked_for(editor: Client, item: Item) -> None:
    """An administrator prints the batch just minted, not the whole estate."""
    Label.objects.create(code=CODE, item=item)
    Label.objects.create(code="ZZZZZZZZZZ", item=item)

    body = editor.get(reverse("label-sheet"), {"code": CODE}).content.decode()

    assert CODE in body
    assert "ZZZZZZZZZZ" not in body


def test_a_code_is_printed_however_it_was_typed(editor: Client, item: Item) -> None:
    """The same folding every other way of naming a code here gets.

    A sheet that answered a lowercase code with a blank page would be the one
    place in this API where the canonical form is the caller's problem.
    """
    Label.objects.create(code=CODE, item=item)

    body = editor.get(reverse("label-sheet"), {"code": CODE.lower()}).content.decode()

    assert f'<p class="code">{CODE}</p>' in body


def test_a_revoked_label_is_never_reprinted(editor: Client, item: Item) -> None:
    """Reprinting one would put back the thing revoking it withdrew."""
    Label.objects.create(code=CODE, item=item, revoked_at=timezone.now())

    body = editor.get(reverse("label-sheet"), {"code": CODE}).content.decode()

    assert CODE not in body
    assert "No labels to print" in body


def test_a_sheet_with_no_codes_named_is_refused(editor: Client, item: Item) -> None:
    """A sheet is a batch about to be stuck on things, not the whole estate.

    Answering the bare path with every live label would lay out one symbol per
    sticker already on a shelf, for nobody, on request.
    """
    Label.objects.create(code=CODE, item=item)

    response = editor.get(reverse("label-sheet"))

    assert response.status_code == 400, response.content
    assert "Name the codes to print" in response.content.decode()


def test_a_sheet_longer_than_a_print_run_is_refused(editor: Client, item: Item) -> None:
    """Every code costs a QR encode, so the bound has to be stated, not assumed.

    Far above a real sheet -- somebody is about to stand up and apply these --
    and there so one request cannot hold a worker for as long as a query
    string can be made long. Same reasoning as MAX_MOVEMENTS.
    """
    Label.objects.create(code=CODE, item=item)
    too_many = ",".join([CODE] * (MAX_SHEET_LABELS + 1))

    response = editor.get(reverse("label-sheet"), {"code": too_many})

    assert response.status_code == 400, response.content
    assert str(MAX_SHEET_LABELS) in response.content.decode()


def test_a_sheet_nobody_may_have_is_refused_as_a_page(client: Client) -> None:
    """The refusal is rendered too: a browser is this endpoint's only client."""
    client.logout()

    response = client.get(reverse("label-sheet"))

    assert response.status_code == 403
    assert response["Content-Type"].startswith("text/html")
    assert "<p>" in response.content.decode()


def test_a_refusal_keeps_the_headers_that_say_what_to_do_about_it() -> None:
    """Only the body is this view's; what DRF put in the headers is part of the refusal.

    Asked of the handler directly because nothing reaching this endpoint sets
    one today -- session authentication offers no ``WWW-Authenticate`` and the
    sheet carries no throttle -- and the point of the loop is that attaching
    either later does not silently lose it.
    """
    response = LabelSheetView().handle_exception(Throttled(wait=30))

    assert response.status_code == 429
    assert response["Retry-After"] == "30"
    assert response["Content-Type"] == "text/html; charset=utf-8"
    assert "Too many submissions" in response.content.decode()
