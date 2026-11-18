"""QR codes, drawn as SVG for a page: a device's registration link, an
administrator's invitation and authenticator, and printed labels
(docs/decisions/0015)."""

import segno
from markupsafe import Markup


def qr_svg(text: str, error: str = "m") -> Markup:
    """A QR code's SVG, with a viewBox so the stylesheet can size it. Always
    a full QR code: segno.make picks a Micro QR for a short text, which not
    every reader reads (docs/decisions/0015)."""
    code = segno.make_qr(text, error=error)
    return Markup(code.svg_inline(scale=5, border=2, unit="px"))
