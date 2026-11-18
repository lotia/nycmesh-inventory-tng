"""How names are kept and searched: canonical spelling (CanonicalName),
keys that compare names (KeyedName), and the folded text searches read
(Searchable), with the functions that make them (docs/decisions/0021)."""

import importlib.metadata
import unicodedata
from typing import ClassVar

from anyascii import anyascii
from sqlalchemy import (
    CheckConstraint,
    ColumnElement,
    SQLColumnExpression,
    Text,
    literal,
    literal_column,
    or_,
)
from sqlalchemy.orm import Mapped, mapped_column, validates

from inventory_tng.catalogue import names, units


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


# Signs a name is stored without, for the spelling Unicode and the SI
# Brochure give (docs/decisions/0021, 2026-11-14): the micro sign as the
# Greek mu, and the one-character degree signs as a degree and a letter.
# NFKC would make them so too, but also m² into m2 and ﬁ into fi.
CANONICAL_SIGNS = str.maketrans({"\u00b5": "\u03bc", "\u2103": "°C", "\u2109": "°F"})


def canonical(name: str) -> str:
    """`name` as it is stored: NFC, so the Ohm, Kelvin and Angstrom signs are
    Ω, K and Å, and without CANONICAL_SIGNS; case and every other character
    as typed. fold() makes the same of either spelling."""
    return unicodedata.normalize("NFC", name.translate(CANONICAL_SIGNS))


class CanonicalName:
    """A row whose `name` an administrator types: kept as canonical() spells
    it, however it is set, so `10µF` typed is `10μF` stored and clashes
    with it (docs/decisions/0021, 2026-11-14)."""

    @validates("name")
    def _canonical(self, _key: str, value: str | None) -> str | None:
        # None is left for NOT NULL to refuse, as a rule broken.
        if value is None:
            return None
        value = canonical(value)
        self._named(value)
        return value

    def _named(self, name: str) -> None:
        """What else follows from `name`, as it is set: nothing, here."""


class KeyedName(CanonicalName):
    """A row whose name is compared by its key (names.name_key), kept in
    `name_key` as the name is set and held to it by a CHECK constraint
    (name_key_check), so a unique index on it compares names so
    (docs/decisions/0021). And by `unit_key`, the key of the name with its
    units read (unit_key), so names that differ only in how a unit is
    spelt are one: written as the session flushes, as reading rules are
    in the database, and again when a rule changes (items.reread); no
    CHECK can hold it, the unit table being the app's (2026-11-15).
    `name_shown` is the name as the app shows it wherever a name is read
    (units.shown_name: each number and the unit or unit's word read after
    it spaced, its thousands so too), written alike, the name as typed kept
    for its form (Ali, 2026-11-15, inventory-tng-ist.3)."""

    name_key: Mapped[str] = mapped_column(Text)
    unit_key: Mapped[str] = mapped_column(Text, info={"not_a_change": True})
    name_shown: Mapped[str] = mapped_column(Text, info={"not_a_change": True})

    def _named(self, name: str) -> None:
        self.name_key = names.name_key(name)


def unit_key(name: str, rules: units.Rules | None = None) -> str:
    """The key of `name` with each unit after a number spelt by what it
    reads as, its rule first (units.read_as): `10 µF`, `10 uF` and `10μF`
    alike, and `3M` as `3m` where a rule reads `M` so."""
    return names.name_key(units.read_as(name, rules))


def name_key_check() -> CheckConstraint:
    return CheckConstraint(
        literal_column("name_key") == names.name_key_sql(literal_column("name")),
        name="name_key",
    )


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
