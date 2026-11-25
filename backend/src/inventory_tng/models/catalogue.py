"""The catalogue's tables: categories, items, their packages and aliases,
reading rules, and vendors."""

from datetime import datetime
from decimal import Decimal
from typing import Annotated

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Index,
    ScalarSelect,
    Select,
    SQLColumnExpression,
    Text,
    event,
    func,
    inspect,
    select,
    text,
)
from sqlalchemy.orm import (
    Mapped,
    Session,
    SessionTransaction,
    aliased,
    column_property,
    declared_attr,
    mapped_column,
    object_session,
)

from inventory_tng.catalogue import units
from inventory_tng.catalogue.amount_rules import COUNTED_WHOLE
from inventory_tng.catalogue.unit_conversion import converted_kept_sql
from inventory_tng.core.db_base import Base
from inventory_tng.core.db_locking import READ_AGAIN_ONCE_LOCKED
from inventory_tng.core.db_protection import protection
from inventory_tng.models.base import (
    MONEY,
    QUANTITY,
    CreatedAt,
    IntId,
    RetiredAt,
    Versioned,
    one_of,
    reference,
    unique_current_name,
    unique_name,
)
from inventory_tng.models.names import (
    CanonicalName,
    KeyedName,
    name_key_check,
    unit_key,
)
from inventory_tng.models.search_text import Searchable, fold, held

UNITS = ("each", "m", "ft")
# A unit of UNITS: an item's, and the one each amount stored apart from its
# item is in, beside it, so a stored amount says its own unit and is never
# read in its item's unit unawares (Ali, 2026-11-24, inventory-tng-2rlr).
Unit = Annotated[str, mapped_column(Text)]


def unit_check(column: str = "unit") -> CheckConstraint:
    """A unit column held to UNITS, the check named for it."""
    return CheckConstraint(one_of(column, UNITS), name=column)


class Category(CanonicalName, Versioned, Base):
    __tablename__ = "category"
    __table_args__ = (
        # Retired ones too, so one can always be brought back
        # (docs/decisions/0019).
        unique_name("category"),
        protection(owner="catalogue/categories.py", lock="catalogue", history=True),
    )

    id: Mapped[IntId]
    created_at: Mapped[CreatedAt]
    name: Mapped[str] = mapped_column(Text)
    retired_at: Mapped[RetiredAt]


class Item(KeyedName, Searchable, Versioned, Base):
    """A catalogue entry, never the stock: stock comes from the ledger, and is
    always counted in `unit`."""

    __tablename__ = "item"
    __table_args__ = (
        # Retired ones too, as for categories (docs/decisions/0019).
        Index("uq_item_name", "name_key", unique=True),
        Index("uq_item_unit_key", "unit_key", unique=True),
        name_key_check(),
        Index(
            "uq_item_manufacturer_part_number",
            text("lower(manufacturer)"),
            text("lower(part_number)"),
            unique=True,
            postgresql_where=text(
                "manufacturer IS NOT NULL AND part_number IS NOT NULL"
            ),
        ),
        Index(
            "uq_item_old_code",
            "old_code",
            unique=True,
            postgresql_where=text("old_code IS NOT NULL"),
        ),
        Index(
            "ix_item_search",
            "search_text",
            postgresql_using="gin",
            postgresql_ops={"search_text": "gin_trgm_ops"},
        ),
        unit_check(),
        CheckConstraint("minimum >= 0", name="minimum"),
        # The app's rule (amount_rules.whole_enough), held here too, as it
        # costs nothing on one row (Ali, 2026-11-23; inventory-tng-gfes).
        CheckConstraint(
            f"NOT {one_of('unit', COUNTED_WHOLE)} OR minimum = trunc(minimum)",
            name="minimum_whole",
        ),
        CheckConstraint("on_order >= 0", name="on_order"),
        protection(owner="catalogue/items.py", lock="catalogue", history=True),
    )

    id: Mapped[IntId]
    created_at: Mapped[CreatedAt]
    name: Mapped[str] = mapped_column(Text)
    category_id: Mapped[int] = reference("category.id")
    # As the manufacturer writes them.
    manufacturer: Mapped[str | None] = mapped_column(Text)
    part_number: Mapped[str | None] = mapped_column(Text)
    # The sheet's NYCM- code.
    old_code: Mapped[str | None] = mapped_column(Text)
    # The unit of its stock, and of minimum and on_order, beside them.
    unit: Mapped[Unit] = mapped_column(server_default="each")
    # The reorder point, compared with total stock across stores. NULL means
    # never reorder.
    minimum: Mapped[Decimal | None] = mapped_column(QUANTITY)
    # Set by whoever orders; checking in a delivery lowers it.
    # A receipt lowers it holding the item's row alone (ledger._receive).
    on_order: Mapped[Decimal | None] = mapped_column(
        QUANTITY, info={"row_lock_only": True}
    )
    notes: Mapped[str | None] = mapped_column(Text)
    ordering_notes: Mapped[str | None] = mapped_column(Text)
    # Whether opening one of its packages makes an open package, followed
    # until used up, or loose units: each package's default
    # (docs/decisions/0020, Amendment).
    track_opened: Mapped[bool] = mapped_column(server_default=text("false"))
    retired_at: Mapped[RetiredAt]

    # Everything a volunteer might type to find an item, as one string,
    # folded (Searchable), and its name alone so, for close names
    # (items.similar), kept alike. ix_item_search makes
    # fuzzy() on search_text fast, as the items list searches it
    # (catalogue/items_routes.py).
    SEARCHED = ("name", "manufacturer", "part_number", "old_code")
    READS_RULES = True
    name_folded: Mapped[str] = mapped_column(Text)

    def refold(
        self,
        rules: units.Rules | None = None,
        written: list[ItemAlias] | None = None,
    ) -> None:
        # Its current aliases too, each folded alone, as the session holds
        # them: refolded as a flush begins (_refold), one added or retired
        # is not yet written, and a new one's id will put it last. Read
        # first: the read may flush, refolding it, which this then replaces.
        # `written`, if given, are its current aliases as written, read for
        # many items at once (items.reread).
        session = object_session(self)
        names = []
        if self.id is not None and session is not None:
            if written is None:
                written = list(session.scalars(current_aliases(self.id)))
            adding = (
                row
                for row in session.new
                if isinstance(row, ItemAlias) and row.item_id == self.id
            )
            names = [
                held(alias.name, rules)
                for alias in (*written, *adding)
                if alias.retired_at is None
            ]
        super().refold(rules)
        self.name_folded = fold(self.name or "")
        self.search_text = " ".join([self.search_text, *names])


def _in_items_unit(
    amount: SQLColumnExpression[Decimal | None],
    unit: SQLColumnExpression[str],
    item_id: SQLColumnExpression[int],
) -> ScalarSelect[Decimal]:
    """A package's `amount`, in its `unit`, in its item's unit, as kept: of
    the item `item_id` names, an alias of its own, so a query that joins
    Item for another reason does not change which."""
    item = aliased(Item)
    return (
        select(converted_kept_sql(amount, unit, item.unit))
        .where(item.id == item_id)
        .correlate_except(item)
        .scalar_subquery()
    )


class ItemPackage(KeyedName, Searchable, Versioned, Base):
    """A size an item comes in: "100-pack" = 100, "reel" = 305 (m)."""

    READS_RULES = True
    __tablename__ = "item_package"
    __table_args__ = (
        # Among all of its item's, retired ones too, so one can always be
        # brought back, compared as items' names are (docs/decisions/0021).
        Index("uq_item_package_name", "item_id", "name_key", unique=True),
        Index("uq_item_package_unit_key", "item_id", "unit_key", unique=True),
        name_key_check(),
        unit_check(),
        CheckConstraint("size > 0", name="size"),
        CheckConstraint("short_below > 0", name="short_below"),
        # A whole one is never a short end.
        CheckConstraint("short_below < size", name="short_below_under_size"),
        CheckConstraint("minimum_sealed >= 0", name="minimum_sealed"),
        protection(owner="catalogue/items.py", lock="catalogue", history=True),
    )

    id: Mapped[IntId]
    created_at: Mapped[CreatedAt]
    item_id: Mapped[int] = reference("item.id")
    name: Mapped[str] = mapped_column(Text)
    # In `unit`.
    size: Mapped[Decimal] = mapped_column(QUANTITY)
    # The unit of size and short_below: its item's when added
    # (_items_unit), and changed with its item's while nothing fixes that
    # (items.save). A change of its own, in its history, that moves its
    # version, so its form drawn in the old unit is refused. Kept as it is
    # when its item's unit is converted between lengths (Ali, 2026-11-24,
    # inventory-tng-y50b): "Reel" holds 305 m still once its item is in ft.
    unit: Mapped[Unit] = mapped_column()
    # NULL: as the item's track_opened says; else this package's own
    # (ledger.tracks_opened).
    track_opened: Mapped[bool | None]
    # Too short to use, in `unit`: an open package with less left
    # is a short end (docs/decisions/0020). NULL: none is.
    short_below: Mapped[Decimal | None] = mapped_column(QUANTITY)
    # How many sealed in the stores to keep: fewer shows on reorder. NULL:
    # no minimum.
    minimum_sealed: Mapped[int | None]
    retired_at: Mapped[RetiredAt]

    # size and short_below in its item's unit, as kept
    # (unit_conversion.converted): what the ledger moves, counts and
    # compares of it, and the scan page is sent. Read with it.
    @declared_attr
    def size_in_items_unit(cls: type[ItemPackage]) -> Mapped[Decimal]:
        return column_property(_in_items_unit(cls.size, cls.unit, cls.item_id))

    @declared_attr
    def short_below_in_items_unit(cls: type[ItemPackage]) -> Mapped[Decimal | None]:
        return column_property(_in_items_unit(cls.short_below, cls.unit, cls.item_id))


# How an item's packages are listed on the item, scan and count pages: the
# smallest first, ties by id, so two of a size keep one order
# (inventory-tng-7mby). The stock and reorder pages list sealed packages
# largest first instead (stock/package_forms.py, largest_first). In its
# item's unit, as an item's packages may be in two.
SMALLEST_PACKAGE_FIRST = (ItemPackage.size_in_items_unit, ItemPackage.id)


class ItemAlias(KeyedName, Base):
    """Another name an item is found by, such as one it was known by: kept
    when an administrator adds it or renames the item, searched as the
    item's own name is (Item.refold), and taken as names are
    (docs/decisions/0021, 2026-11-14 and 2026-11-15).
    Its key is unique among current aliases here; among items' names too,
    which catalogue/items.py checks holding the catalogue lock. Removed, it is
    retired, freeing the name; never changed otherwise (the initial
    migration)."""

    __tablename__ = "item_alias"
    __table_args__ = (
        Index(
            "uq_item_alias_name",
            "name_key",
            unique=True,
            postgresql_where=text("retired_at IS NULL"),
        ),
        Index(
            "uq_item_alias_unit_key",
            "unit_key",
            unique=True,
            postgresql_where=text("retired_at IS NULL"),
        ),
        name_key_check(),
        protection(owner="catalogue/items.py", lock="catalogue", history=True),
    )

    id: Mapped[IntId]
    created_at: Mapped[CreatedAt]
    item_id: Mapped[int] = reference("item.id")
    name: Mapped[str] = mapped_column(Text)
    retired_at: Mapped[RetiredAt]
    # Its name folded, as an item's name_folded, for close names
    # (items.similar), set as the name is: an alias's never changes.
    name_folded: Mapped[str] = mapped_column(Text)

    def _named(self, name: str) -> None:
        super()._named(name)
        self.name_folded = fold(name)


# Whether an alias is current: not removed.
CURRENT_ALIAS = ItemAlias.retired_at.is_(None)


def current_aliases(item_id: int) -> Select[ItemAlias]:
    """An item's current aliases, oldest first, as its search_text holds
    them."""
    query = select(ItemAlias).where(ItemAlias.item_id == item_id, CURRENT_ALIAS)
    return query.order_by(ItemAlias.id)


class ReadingRule(Versioned, Base):
    """How a unit, as typed after a number, is read, settled by an
    administrator (units.Rule, docs/decisions/0021, 2026-11-15): `token`,
    such as `M`, case kept, read as `reading`, a unit as str(units.Reading) spells
    it, or as no unit (NULL); and whether a name typed with it spells it by
    its symbol (`respell`). Looked up before the unit table wherever a unit
    is read. Changed or deleted by an administrator, when every name with
    it is read again (items.reread), so it is the catalogue's to lock."""

    __tablename__ = "reading_rule"
    __table_args__ = (
        Index("uq_reading_rule_token", "token", unique=True),
        CheckConstraint("reading IS NOT NULL OR NOT respell", name="respell_a_unit"),
        protection(owner="catalogue/reading_rules.py", lock="catalogue", history=True),
    )

    id: Mapped[IntId]
    created_at: Mapped[CreatedAt]
    token: Mapped[str] = mapped_column(Text)
    reading: Mapped[str | None] = mapped_column(Text)
    respell: Mapped[bool] = mapped_column(server_default=text("false"))
    # Who settled it last, and when; no one, if the import did.
    decided_by_admin_id: Mapped[int | None] = reference("admin.id")
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    @property
    def rule(self) -> units.Rule:
        return units.Rule(self.reading, self.respell)


# The reading rules a transaction has read (reading_rules_held), kept
# until one is written or a savepoint rolled back, so a request reads them
# once.
RULES_INFO = "inventory.reading_rules"
# Read before the catalogue lock, as a form's answers are, they are read
# again under it: a rule changed meanwhile reads names otherwise.
READ_AGAIN_ONCE_LOCKED.add(RULES_INFO)


def reading_rules_held(session: Session) -> units.Rules:
    """Every reading rule, by its token: read once, and again after a write
    to them, a rollback or the transaction's end."""
    held = session.info.get(RULES_INFO)
    if held is None:
        rows = session.execute(
            select(ReadingRule.token, ReadingRule.reading, ReadingRule.respell)
        )
        held = {token: units.Rule(reading, respell) for token, reading, respell in rows}
        session.info[RULES_INFO] = held
    return held


@event.listens_for(Session, "after_flush")
def _rules_written(session: Session, _context: object) -> None:
    changed = (*session.new, *session.dirty, *session.deleted)
    if any(isinstance(row, ReadingRule) for row in changed):
        session.info.pop(RULES_INFO, None)


@event.listens_for(Session, "after_soft_rollback")
def _rules_rolled_back(session: Session, _transaction: object) -> None:
    session.info.pop(RULES_INFO, None)


@event.listens_for(Session, "after_transaction_end")
def _rules_forgotten(session: Session, transaction: SessionTransaction) -> None:
    if transaction.parent is None:
        session.info.pop(RULES_INFO, None)


# Every searchable row's, kept here as it reads items, aliases and reading
# rules (search_text.Searchable, names.KeyedName). First, so the lock guard and
# the history see the folded columns too. A row changed in nothing searched is
# left alone, loading nothing, so a change such as a receipt's to on_order
# stays only that (db_protection.flush_writes).
@event.listens_for(Session, "before_flush", insert=True)
def _refold(session: Session, _context: object, _instances: object) -> None:
    changed = (*session.new, *session.dirty)
    rows: list[Searchable] = [
        row
        for row in changed
        if isinstance(row, Searchable)
        and (
            row in session.new
            or any(
                inspect(row).attrs[field].history.has_changes()
                for field in row.SEARCHED
            )
        )
    ]
    # An alias added or removed is searched as its item's (Item.refold).
    # And each name new or changed keyed with its units read, and spaced to
    # be shown, by the reading rules (KeyedName).
    rows += [
        session.get_one(Item, row.item_id)
        for row in changed
        if isinstance(row, ItemAlias)
    ]
    keyed = [
        row
        for row in changed
        if isinstance(row, KeyedName)
        and (row in session.new or inspect(row).attrs["name"].history.has_changes())
    ]
    rules = None
    if keyed or any(row.READS_RULES for row in rows):
        rules = reading_rules_held(session)
    for row in dict.fromkeys(rows):
        row.refold(rules)
    for row in keyed:
        row.unit_key = unit_key(row.name or "", rules)
        row.name_shown = units.shown_name(row.name or "", rules)


@event.listens_for(Session, "before_flush")
def _items_unit(session: Session, _context: object, _instances: object) -> None:
    """A package or vendor offer added is in its item's unit, unless given
    another."""
    for row in session.new:
        if isinstance(row, ItemPackage | VendorOffer) and row.unit is None:
            row.unit = session.get_one(Item, row.item_id).unit


class Vendor(Base):
    __tablename__ = "vendor"
    __table_args__ = (
        unique_current_name("vendor"),
        protection(owner=None, lock="row", history=True),
    )

    id: Mapped[IntId]
    created_at: Mapped[CreatedAt]
    name: Mapped[str] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(Text)
    retired_at: Mapped[RetiredAt]


class VendorOffer(Base):
    """Where to buy an item."""

    __tablename__ = "vendor_offer"
    __table_args__ = (
        CheckConstraint("units_per_order > 0", name="units_per_order"),
        unit_check(),
        protection(owner=None, lock="row", history=True),
    )

    id: Mapped[IntId]
    created_at: Mapped[CreatedAt]
    item_id: Mapped[int] = reference("item.id")
    vendor_id: Mapped[int | None] = reference("vendor.id")
    url: Mapped[str] = mapped_column(Text)
    vendor_sku: Mapped[str | None] = mapped_column(Text)
    units_per_order: Mapped[Decimal | None] = mapped_column(QUANTITY)
    # The unit of units_per_order, kept as an item package's is.
    unit: Mapped[Unit]
    retired_at: Mapped[RetiredAt]


class VendorPrice(Base):
    """Price history, kept rather than overwritten: the current price is the
    latest row."""

    __tablename__ = "vendor_price"
    __table_args__ = (
        CheckConstraint("price >= 0", name="price"),
        protection(owner=None, lock="row", history=True),
    )

    id: Mapped[IntId]
    created_at: Mapped[CreatedAt]
    vendor_offer_id: Mapped[int] = reference("vendor_offer.id")
    price: Mapped[Decimal] = mapped_column(MONEY)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
