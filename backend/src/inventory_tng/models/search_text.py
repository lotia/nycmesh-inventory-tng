"""The text searches read and how they match it: fold and the forms it
keeps (folds, held, in_ascii, in_ascii_once), what each searchable row
keeps (Searchable, FOLDED_WITH), and the matches (holds, fuzzy;
docs/decisions/0016, 0021)."""

import importlib.metadata
import unicodedata
from typing import ClassVar

from anyascii import anyascii
from sqlalchemy import ColumnElement, SQLColumnExpression, Text, literal, or_
from sqlalchemy.orm import Mapped, mapped_column

from inventory_tng.catalogue import units


def holds(column: SQLColumnExpression[str], search: str) -> ColumnElement[bool]:
    """Whether `column` holds `search` as typed, ignoring case: ILIKE, which
    a gin_trgm_ops index serves, as icontains's lower() LIKE is not."""
    escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return column.ilike(f"%{escaped}%", escape="\\")


def in_ascii(text: str) -> str:
    """`text` with compatibility characters made their canonical ones
    (NFKC), then in ASCII as anyascii spells it: what fold() and folds() do
    last."""
    return anyascii(unicodedata.normalize("NFKC", text))


def fold(text: str, rules: units.Rules | None = None) -> str:
    """`text` as searches compare it: in ASCII, as anyascii spells it, so
    "cafe" finds "Café" and "lodz" "Łódź" (docs/decisions/0021, 2026-11-12),
    after compatibility characters are made their canonical ones (NFKC), so
    "ﬁ" is "fi", and each unit right after a number is spelt by its symbol
    (units.respell, 2026-11-14), so "10 µF", "10μF" and "10uf" are all
    "10uF", as `rules` read it first. What is searched is kept so and as
    typed (held); what is typed is matched so and as typed (folds)."""
    decomposed = unicodedata.normalize("NFKD", text)
    return in_ascii(units.respell(decomposed, rules))


def folds(text: str, rules: units.Rules | None = None) -> tuple[str, ...]:
    """`text` folded as typed, units as they are, then by fold() if that
    differs: searches match either (Ali, 2026-11-15, inventory-tng-c0c.2),
    so a unit's word alone ("feet"), a unit typed shorter than a token not
    one ("48 V" in "48 VDC") and a unit half typed ("10 μ") find what they
    found before units were read, and "10 µF" finds "10uf" too."""
    decomposed = unicodedata.normalize("NFKD", text)
    return in_ascii_once(decomposed, units.respell(decomposed, rules))


def in_ascii_once(typed: str, respelt: str) -> tuple[str, ...]:
    """`typed` in ASCII (in_ascii), then `respelt` so if that differs, case
    aside, as search ignores it: the forms folds() keeps, and a search
    group (units.groups) is matched by."""
    forms = in_ascii(typed), in_ascii(respelt)
    return forms[:1] if forms[1].lower() == forms[0].lower() else forms


def held(text: str, rules: units.Rules | None = None) -> str:
    """What search_text keeps of `text`: each of its folds, spaced."""
    return " ".join(folds(text, rules))


# What folds (fold), as each folded row records it (Searchable.folded_with),
# so rows a release of anyascii, a Python with a newer Unicode, or another
# unit table would spell otherwise can be found.
FOLDED_WITH = (
    f"anyascii {importlib.metadata.version('anyascii')}, "
    f"Unicode {unicodedata.unidata_version}, units {units.VERSION}"
)


class Searchable:
    """A row a search finds: `search_text`, what it is found by (SEARCHED,
    its name unless a model says more), spaced, each field as held() keeps
    it, and `folded_with`, what folded it (FOLDED_WITH). Kept by the app as
    each row is added or changed (catalogue._refold), never by PostgreSQL
    (docs/decisions/0016)."""

    SEARCHED: ClassVar[tuple[str, ...]] = ("name",)
    # Whether reading rules read its units (an item's, a package's), as
    # items.reread folds it again when one changes.
    READS_RULES: ClassVar[bool] = False
    # Not a change alone: what is searched changes with it, or another row
    # does, as an item's aliases (Item.refold), which keep their own history.
    search_text: Mapped[str] = mapped_column(Text, info={"not_a_change": True})
    folded_with: Mapped[str] = mapped_column(Text, info={"not_a_change": True})

    def refold(self, rules: units.Rules | None = None) -> None:
        # Each folded alone, so no field's number reads the next's start
        # as a unit.
        rules = rules if self.READS_RULES else None
        fields = (held(getattr(self, field) or "", rules) for field in self.SEARCHED)
        self.search_text = " ".join(fields)
        self.folded_with = FOLDED_WITH


def fuzzy(column: SQLColumnExpression[str], search: str) -> ColumnElement[bool]:
    """Whether `column` matches what was typed, forgiving typos: it holds it
    as typed, or pg_trgm finds it close, as a whole (`%`) or as a word in it
    (`<%`, so "litebeem" finds "LiteBeam 5AC")."""
    return or_(
        holds(column, search),
        column.op("%", is_comparison=True)(search),
        literal(search).op("<%", is_comparison=True)(column),
    )
