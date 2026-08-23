"""The printed label: what is encoded on it, and how it is drawn to be printed.

"Half the QR codes don't scan" is a printing failure, and no library choice
fixes it -- so the decisions that make a label readable are made here, once,
and asserted by tests rather than left in prose. Decision 0011 section 3 is the
argument; this module is the implementation and does not repeat it.

Four of those decisions are load-bearing, and each is a named constant below:
the error correction level, the quiet zone, the size of the printed sticker,
and the floor under the module size that the first three have to clear. The
last one is the guard rail: shrink the sticker, or lengthen the hostname until
the symbol needs another version, and the module size falls below what a laser
printer and a phone camera can be trusted with. That fails loudly here rather
than quietly on a shelf.

The payload is uppercase -- ``HTTPS://INVENTORY.NYCMESH.NET/S/7QK3M2XV9A`` --
which decision 0011 section 3 argues for on the encoding it buys. The host is
deployment-varying and is read from the environment; see ``LABEL_BASE_URL`` in
settings and .env.sample.
"""

import datetime
from collections.abc import Iterable
from dataclasses import dataclass

import segno
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.html import format_html
from django.utils.safestring import SafeString, mark_safe

from inventory.models import Label

# Level Q, not the L or M every generator defaults to. ISO/IEC 18004:2024
# clause 5.1 f) puts the four levels at 7 %, 15 %, 25 % and 30 % of codewords
# recoverable; Q is the 25 % one. These labels live on a shelf in a basement
# and are handled with dirty hands, which is the case the level exists for.
ERROR_CORRECTION = "Q"

# The blank margin around the symbol, in modules. ISO/IEC 18004 clause 5.3.8
# requires four on all four sides, and it is drawn as part of the SVG rather
# than left to whatever the sticker is pasted onto.
QUIET_ZONE_MODULES = 4

# The sticker. The physical size is the thing that is chosen; the module size
# below is whatever falls out of fitting the symbol to it.
LABEL_WIDTH_MM = 30.0

# The floor under that module size. The standard deliberately does not set one
# -- its introduction says "module dimensions are user-specific to enable
# symbol production by a wide variety of techniques" -- so this is an
# application decision, and the number is sized for the two devices in the
# story: a 600 dpi office laser printer, where 0.6 mm is about fourteen dots
# across a module and ink spread is a fraction of one, and a phone camera held
# at arm's length. Lower it and the labels this project exists to replace come
# back.
MINIMUM_MODULE_MM = 0.6

# The characters QR alphanumeric mode can encode, from ISO/IEC 18004:2024
# clause 5.1 b) 2): the digits, the uppercase letters, and nine others. A
# payload straying outside this set is encodable only in byte mode, at eight
# bits a character instead of five and a half, which is a bigger symbol at the
# same sticker size -- so it is refused rather than silently encoded.
ALPHANUMERIC = frozenset("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ $%*+-./:")

# The deep link's path, from decision 0011 section 3. One letter, because every
# character shrinks the modules at a fixed sticker size, and small modules are
# what faded ink destroys first.
DEEP_LINK_PATH = "S"


@dataclass(frozen=True)
class PrintedLabel:
    """One sticker, ready to be drawn.

    The code and the date appear on the sticker as text as well as inside the
    symbol: a dead QR is still a working label if a volunteer can read the code
    off it and type it, and a batch whose ink has faded can be replaced as a
    set if each one says when it was printed.
    """

    code: str
    printed_on: datetime.date
    symbol: SafeString


def deep_link(code: str) -> str:
    """What the symbol encodes: the app's own URL for this code, uppercased.

    Uppercase throughout, which is safe because the scheme and host are
    case-insensitive and the code's alphabet has no lowercase in it.
    """
    base = settings.LABEL_BASE_URL.rstrip("/")
    return f"{base}/{DEEP_LINK_PATH}/{code}".upper()


def symbol_for(code: str) -> segno.QRCode:
    """The QR symbol for one code, at the level and in the mode chosen above.

    Both are stated rather than left to the encoder. ``segno`` picks a mode by
    inspecting the data, and raises the error correction level when there is
    spare room in the chosen version -- either of which would make the printed
    labels something other than what was decided, without anybody noticing
    until a batch is on a shelf.
    """
    payload = deep_link(code)
    outside = sorted(set(payload) - ALPHANUMERIC)
    if outside:
        raise ImproperlyConfigured(
            f"LABEL_BASE_URL produces the label payload {payload!r}, which contains "
            f"{''.join(outside)!r}. QR alphanumeric mode encodes only digits, uppercase "
            "letters, space and $%*+-./:, so a host outside that set cannot be printed."
        )
    return segno.make_qr(payload, error=ERROR_CORRECTION, mode="alphanumeric", boost_error=False)


def module_mm(symbol: segno.QRCode) -> float:
    """How big one module comes out at, once the symbol is fitted to a sticker.

    Derived rather than configured, so the sticker size and the symbol size
    cannot disagree. Raises rather than printing something unreadable: the
    symbol grows a version when the hostname grows, and at a fixed sticker size
    that is the module size shrinking.
    """
    across = symbol.symbol_size(scale=1, border=QUIET_ZONE_MODULES)[0]
    size = LABEL_WIDTH_MM / across
    if size < MINIMUM_MODULE_MM:
        raise ImproperlyConfigured(
            f"A version {symbol.version} symbol and a {QUIET_ZONE_MODULES}-module quiet zone is "
            f"{across} modules across, which on a {LABEL_WIDTH_MM} mm label is {size:.2f} mm per "
            f"module -- below the {MINIMUM_MODULE_MM} mm floor in inventory.labels. Either the "
            "label is too small or LABEL_BASE_URL is too long to print at this size."
        )
    return size


def svg_for(code: str) -> SafeString:
    """One symbol as an inline SVG, sized in millimetres.

    Millimetres, not pixels: this is printed, and a length in device pixels
    means whatever the browser's print pipeline decides it means. The quiet
    zone is inside the viewBox, so it survives being pasted next to anything.

    Drawn here rather than through segno's own SVG writer because the geometry
    is the part under test -- one ``M x y h n v 1 h -n z`` per run of dark
    modules, from which the matrix can be read straight back out and decoded.
    """
    symbol = symbol_for(code)
    # Nothing below is drawn from the result: the viewBox is in modules and the
    # width is the sticker. This is where the floor under the module size is
    # enforced, and drawing a label that fails it is the failure this whole
    # module exists to prevent, so it happens before anything is drawn.
    module_mm(symbol)
    width = LABEL_WIDTH_MM
    matrix = symbol.matrix
    across = len(matrix) + 2 * QUIET_ZONE_MODULES
    runs: list[str] = []
    for y, row in enumerate(matrix):
        x = 0
        while x < len(row):
            if not row[x]:
                x += 1
                continue
            end = x
            while end < len(row) and row[end]:
                end += 1
            runs.append(f"M{x + QUIET_ZONE_MODULES} {y + QUIET_ZONE_MODULES}h{end - x}v1h-{end - x}z")
            x = end
    # Escaped through format_html rather than concatenated: the code reaches
    # the accessible name, and the alphabet is not this function's to trust.
    return format_html(
        '<svg xmlns="http://www.w3.org/2000/svg" width="{width}mm" height="{width}mm" '
        'viewBox="0 0 {across} {across}" shape-rendering="crispEdges" role="img" '
        'aria-label="QR code for label {code}">'
        '<rect width="{across}" height="{across}" fill="#ffffff"/>'
        '<path fill="#000000" d="{runs}"/></svg>',
        width=f"{width:g}",
        across=across,
        code=code,
        # Safe by construction: every run is formatted from two integers.
        runs=mark_safe("".join(runs)),
    )


def printed(label: Label) -> PrintedLabel:
    """One stored label, as the sheet needs it.

    The date is the label's own ``printed_at`` rather than today's, so two
    copies of one sticker never disagree about the age of the batch they
    belong to.
    """
    return PrintedLabel(
        code=label.code,
        printed_on=timezone.localtime(label.printed_at).date(),
        symbol=svg_for(label.code),
    )


def sheet(labels: Iterable[Label]) -> str:
    """A printable page of stickers, as a self-contained HTML document.

    Takes anything it can walk rather than a queryset, because walking is all
    it does -- and the caller evaluates the rows once, so that counting them
    and drawing them are not two trips to the database.

    HTML rather than PDF: every deployment already has a browser, print
    dialogs handle paper size and margins better than a server can guess at
    them, and a PDF library is a dependency this does not need. The lengths in
    it are all in millimetres, so what comes out of the printer is the size it
    says it is.
    """
    printable = [printed(label) for label in labels]
    return render_to_string(
        "inventory/label_sheet.html",
        {
            "labels": printable,
            "generated_on": timezone.localdate(),
            "label_width_mm": f"{LABEL_WIDTH_MM:g}",
            "error_correction": ERROR_CORRECTION,
        },
    )


def refusal_page(detail: str) -> SafeString:
    """A refusal, as the page this endpoint answers everything else with.

    Here rather than in the view because this module owns what a label sheet
    looks like, and a refusal is the sheet not being there. One sentence and
    nothing else: a browser is the only client this endpoint has, and the
    sentence is DRF's own.
    """
    return format_html(
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        "<title>Label sheet</title></head><body><p>{}</p></body></html>",
        detail,
    )
