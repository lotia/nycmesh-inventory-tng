"""How a name an administrator types is kept and compared: its canonical
spelling (canonical, CanonicalName), and the keys that compare names
(KeyedName, unit_key, name_key_check; docs/decisions/0021). How names are
searched is search_text.py."""

import unicodedata

from sqlalchemy import CheckConstraint, Text, literal_column
from sqlalchemy.orm import Mapped, mapped_column, validates

from inventory_tng.catalogue import name_keys, units

# Signs a name is stored without, for the spelling Unicode and the SI
# Brochure give (docs/decisions/0021, 2026-11-14): the micro sign as the
# Greek mu, and the one-character degree signs as a degree and a letter.
# NFKC would make them so too, but also m² into m2 and ﬁ into fi.
CANONICAL_SIGNS = str.maketrans({"\u00b5": "\u03bc", "\u2103": "°C", "\u2109": "°F"})


def canonical(name: str) -> str:
    """`name` as it is stored: NFC, so the Ohm, Kelvin and Angstrom signs are
    Ω, K and Å, and without CANONICAL_SIGNS; case and every other character
    as typed. search_text.fold() makes the same of either spelling."""
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
    """A row whose name is compared by its key (name_keys.name_key), kept in
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
        self.name_key = name_keys.name_key(name)


def unit_key(name: str, rules: units.Rules | None = None) -> str:
    """The key of `name` with each unit after a number spelt by what it
    reads as, its rule first (units.read_as): `10 µF`, `10 uF` and `10μF`
    alike, and `3M` as `3m` where a rule reads `M` so."""
    return name_keys.name_key(units.read_as(name, rules))


def name_key_check() -> CheckConstraint:
    return CheckConstraint(
        literal_column("name_key") == name_keys.name_key_sql(literal_column("name")),
        name="name_key",
    )
