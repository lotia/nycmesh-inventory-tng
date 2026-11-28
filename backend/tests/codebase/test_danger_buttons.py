"""A danger button is only ever a confirmation page's, written by the
_confirm.html macro (docs/decisions/0019, Actions), so every such page
marks its way back data-settle: the page's script waits as the page
loads, an ignored tap focusing that link (Ali, 2026-11-27,
inventory-tng-0c2z; docs/decisions/0019, Double taps). A hand-written
copy would leave its page taking the second tap of the double tap that
brought it up."""

from helpers.markup import read
from helpers.source import TEMPLATES

CONFIRM = TEMPLATES / "_confirm.html"


def test_only_the_confirm_macro_writes_a_danger_button() -> None:
    written = [
        str(path.relative_to(TEMPLATES))
        for path in sorted(TEMPLATES.rglob("*.html"))
        if path != CONFIRM and read(path.read_text()).all("*", class_="danger")
    ]
    assert written == []


def test_the_confirm_macro_marks_its_way_back() -> None:
    assert read(CONFIRM.read_text()).all("a", data_settle="") != []
