"""What models.fold made of a wide sample of text, kept in
tests/fold_reference.tsv, so a release of anyascii, or a Python with a
newer Unicode, that spells something otherwise fails test_fold_reference
with the difference, rather than leaving rows folded the old way
(docs/decisions/0021, 2026-11-12).

Run from backend/, to write the file again, or with --check to compare
only, writing nothing, and exit 1 with the difference if it is stale:

    uv run python -m tests.helpers.fold_reference [--check]

`mise run regenerate-fold-reference` runs the first.
"""

import argparse
import sys
import unicodedata
from pathlib import Path

from inventory_tng.models import FOLDED_WITH, fold

from . import committed

REFERENCE = Path(__file__).parent.parent / "fold_reference.tsv"
COMMANDS = (
    "mise run regenerate-fold-reference, or from backend/: "
    "uv run python -m tests.helpers.fold_reference"
)

# Unicode's blocks a name may well hold a character of, first to last.
BLOCKS = {
    "Latin-1 Supplement": (0x00A0, 0x00FF),
    "Latin Extended-A": (0x0100, 0x017F),
    "Latin Extended-B": (0x0180, 0x024F),
    "Greek and Coptic": (0x0370, 0x03FF),
    "Cyrillic": (0x0400, 0x04FF),
    "General Punctuation": (0x2000, 0x206F),
    "Currency Symbols": (0x20A0, 0x20CF),
    "Letterlike Symbols": (0x2100, 0x214F),
}
# Whole strings, as names are typed or pasted.
STRINGS = (
    "Café Łódź patch",
    "Câble Ærø Straße",
    "Ubiquiti LiteBeam 5AC Gen2",
    "Zoë Ångström",
    "Søren Ødegård",
    "Œuvre façade naïve",
    "café",
    "Café Lódź",
    "è́̂",
    "́",
    "ﬁber ﬂoor ﬀ ﬃ ﬄ",
    "ｃａｆｅ ＡＢ１２",  # noqa: RUF001  Look-alikes, sampled.
    "１０ｍ ＲＪ４５",  # noqa: RUF001  Look-alikes, sampled.
    "10µF 10μF",
    "½ ¼ ¾ in",
    "2×4 30° ±5%",  # noqa: RUF001  Look-alikes, sampled.
    "Москва кабель",
    "Αθήνα καλώδιο",
    "Київ",
    "Ελληνικά ΟΣ",
    "北京 東京",
    "ケーブル",
    "서울 케이블",
    "مرحبا",
    "שלום",
    "हिन्दी",
    "Việt Nam cáp",
    "İstanbul ışık",  # noqa: RUF001  Look-alikes, sampled.
    "Dvořák Škoda",
    "Kraków Gdańsk",
    "Reykjavík Þór",
    "“Quoted” ‘single’ — dash – en … ",  # noqa: RUF001  Look-alikes, sampled.
    "Cat6 \N{GRINNING FACE} ok",
    "™ ® © № ℃",
    "€5 £3 ¥7 ₿1 ₹2",
)


def characters() -> list[str]:
    """Every character assigned in BLOCKS, but controls."""
    return [
        character
        for first, last in BLOCKS.values()
        for character in map(chr, range(first, last + 1))
        if unicodedata.category(character) not in {"Cn", "Cc"}
    ]


def shown(text: str) -> str:
    """`text` as one field of a line: what would break the line or hide in
    it escaped, spaces at its end too, which an editor might trim."""
    escaped = "".join(
        character
        if character.isprintable() and character != "\\"
        else character.encode("unicode_escape").decode()
        for character in text
    )
    kept = escaped.rstrip(" ")
    return kept + "\\x20" * (len(escaped) - len(kept))


def folded(text: str) -> str:
    """What `text` folds to, as a field: ∅ for nothing, which fold, all
    ASCII, never gives, so no line ends in a tab an editor might trim."""
    return shown(fold(text)) or "∅"


def reference() -> str:
    """The file, as fold folds now. Its first line names anyascii's version
    and Python's Unicode's (FOLDED_WITH): which characters there are, and
    what NFKC makes of them."""
    lines = [
        f"# models.fold, with {FOLDED_WITH}: each input, its code point, what "
        + f"it folds to (∅: nothing). Written by {COMMANDS}."
    ]
    lines += [
        f"{shown(each)}\tU+{ord(each):04X}\t{folded(each)}" for each in characters()
    ]
    lines += [f"{shown(each)}\tstring\t{folded(each)}" for each in STRINGS]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--check", action="store_true", help="compare only, and write nothing"
    )
    if parser.parse_args(argv).check:
        return committed.check(REFERENCE, reference())
    return committed.write(REFERENCE, reference())


if __name__ == "__main__":
    sys.exit(main())
