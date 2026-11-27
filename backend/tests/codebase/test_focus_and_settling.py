"""The frontend's double-tap rule (docs/decisions/0019, Double taps; Ali,
2026-11-27, inventory-tng-r3jx) goes through frontend/src/shared/settling.ts:
focus moves by focusInPlace, without scrolling, or by focusInView where a
tap asked to go to what it focuses; and an ignored tap is told from a key's
press there, or in the scan page's hooks built on it. So no other module
calls focus() itself, which scrolls, nor reads a click's detail, as a
guard of its own would.

Read from the source, as these are calls a rendered page cannot show for
every branch. A hand-rolled guard's timer cannot be told from the page's
other timers (retries, the camera's reads) without guessing, so it is
left to review; its pointer-or-key test is what this catches."""

import re
from pathlib import Path

import pytest

from inventory_tng.core.local_env import CLONE

pytestmark = pytest.mark.clone

SOURCE = CLONE / "frontend/src"
SETTLING = SOURCE / "shared/settling.ts"
HOOKS = SOURCE / "scan/settling-hooks.tsx"
# focus(), called or optionally called, or its scroll option named.
FOCUSES = re.compile(r"\.focus\s*(?:\?\.)?\(|\bpreventScroll\b")
# A click's detail compared, which tells a pointer's tap from a key's press.
TELLS_TAPS = re.compile(r"\.detail\s*(?:[<>]=?|[!=]==?)")


def _sources() -> list[Path]:
    return [
        path
        for path in [*SOURCE.rglob("*.ts"), *SOURCE.rglob("*.tsx")]
        if ".test." not in path.name and "testing" not in path.parts
    ]


def _holding(pattern: re.Pattern[str], allowed: set[Path]) -> list[str]:
    return sorted(
        path.relative_to(SOURCE).as_posix()
        for path in _sources()
        if path not in allowed and pattern.search(path.read_text())
    )


def test_focus_moves_only_through_settling() -> None:
    assert _holding(FOCUSES, {SETTLING}) == [], (
        "focus with focusInPlace, or focusInView where the tap asked to go"
        " there (frontend/src/shared/settling.ts)"
    )


def test_taps_are_told_from_key_presses_only_in_settling() -> None:
    assert _holding(TELLS_TAPS, {SETTLING, HOOKS}) == [], (
        "ignore taps with ignoreTaps (frontend/src/shared/settling.ts) or the"
        " scan page's settling hooks"
    )


@pytest.mark.parametrize(
    "text",
    [
        "box.focus();",
        "list.current?.focus();",
        "to?.focus?.()",
        "button.focus({ preventScroll: true });",
    ],
)
def test_each_focus_is_found(text: str) -> None:
    assert FOCUSES.search(text)


@pytest.mark.parametrize(
    "text",
    ["if (event.detail > 0) {", "event.detail === 0", "e.detail >= 1"],
)
def test_each_pointer_test_is_found(text: str) -> None:
    assert TELLS_TAPS.search(text)


def test_a_reply_detail_is_left() -> None:
    assert not TELLS_TAPS.search("refused(reply.body.detail, entry.body)")
