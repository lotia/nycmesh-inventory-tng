"""The scan page's catalogue reply schemas (scan/catalogue_api.py)."""

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class Entry(BaseModel):
    """Read straight from a row's attributes."""

    model_config = ConfigDict(from_attributes=True)


class VolunteerEntry(Entry):
    id: int
    name: str


class LocationEntry(Entry):
    id: int
    name: str
    kind: str
    parent_id: int | None


class SealedAt(BaseModel):
    location_id: int
    # Whole packages.
    count: int


class PackageEntry(Entry):
    id: int
    # As the app shows it (models.names.KeyedName.name_shown), as are items'
    # and retired ones' names here: searched by search_text, which holds it as
    # typed.
    name: str
    # In the item's unit.
    size: Decimal
    # Whether opening one makes an open package, followed until used up, or
    # loose units: its own setting, else its item's (docs/decisions/0020).
    track_opened: bool
    # Retired, but with sealed packages left: they may be taken, never
    # received.
    retired: bool
    # Too short to use, in the item's unit, if set: for an open package
    # the page makes before the server has seen it (OpenPackageEntry).
    short_below: Decimal | None
    # The current places with some sealed, and how many: opening its item
    # at one of them opens this package, if it is the item's only one
    # sealed there (the page takes off what its outbox has moved since).
    sealed: list[SealedAt]


class OpenPackageEntry(Entry):
    """A package opened and followed, not reported used up: one a cart can
    take from where it is."""

    id: int
    key: uuid.UUID
    item_id: int
    package_id: int
    # Its package's name, sent here as its package may not be: a retired
    # one with none sealed left is not (takeable_package).
    package_name: str
    location_id: int
    opened_at: datetime
    # What is left on it, in the item's unit, as the records say.
    left: Decimal
    # Its package's too-short-to-use length, in the item's unit, if set,
    # retired package or not: a cut leaving less than it ticks "nothing
    # usable left" (docs/decisions/0020).
    short_below: Decimal | None


class RetiredPackage(BaseModel):
    id: int
    name: str
    size: Decimal


class ItemEntry(BaseModel):
    id: int
    name: str
    unit: str
    category: str
    manufacturer: str | None
    part_number: str | None
    old_code: str | None
    # Its details and category, as searches keep them
    # (models.search_text.held): matched, never shown (0021). The category is
    # folded here, as it is read, on purpose: categories keep no folded column
    # for so few names.
    search_text: str
    # Its current aliases (models.catalogue.ItemAlias), as spelt: other names
    # it is found by, matched, never shown. Its search_text holds them folded.
    aliases: list[str]
    packages: list[PackageEntry]
    # Retired, but with stock left: it may be taken, never received.
    retired: bool
    # Its retired packages with none sealed left: named, never offered.
    retired_packages: list[RetiredPackage]


class RetiredItem(BaseModel):
    """A retired item with no stock left: named, never offered."""

    id: int
    name: str
    unit: str
    packages: list[RetiredPackage]


class LabelEntry(Entry):
    """A printed code, and the one thing it names."""

    code: str
    item_id: int | None
    package_id: int | None
    location_id: int | None


class Settings(Entry):
    """How the page behaves, as administrators set it
    (models.site_settings.ScanSettings)."""

    name_check_minutes: int


class ReadingRuleEntry(BaseModel):
    """A reading rule (models.catalogue.ReadingRule): a unit as typed after a
    number (units.rule_key), and what it reads as, by its symbol as searches
    spell it (str(units.Reading)), or null: no unit. The page reads units by
    them before its table, as the server does (units.respell)."""

    token: str
    reading: str | None


class Catalogue(BaseModel):
    settings: Settings
    volunteers: list[VolunteerEntry]
    locations: list[LocationEntry]
    items: list[ItemEntry]
    labels: list[LabelEntry]
    open_packages: list[OpenPackageEntry]
    retired_items: list[RetiredItem]
    # Optional, so a page of the build before reads the reply as it did.
    reading_rules: list[ReadingRuleEntry] = []
    # Of the batch keys the page asked about, those saved: what the rest of
    # the reply shows already (with_saved), so the page takes off only what
    # the others moved (inventory-tng-jlc).
    saved: list[uuid.UUID] = []
