"""Turning rule 1's answers into catalogue rows, identifiers, and a review list.

The leading underscore keeps this out of Django's command list. It is the work
`mint_items` performs and the whole-import command will reuse, not something
anybody runs by name. Here rather than beside the models it writes because
`inventory/sheet/items.py` is the rule it applies and that package pulls in a
spreadsheet reader; `inventory/staging.py` states why nothing imported at boot
may do that.

## What one run produces

Rule 1 answers per item string. This turns each of its answers into a row:

- **A catalogued name** becomes an `Item`, because an identifier has to name
  one and nothing else in the import mints them.
- **A string that resolves** becomes an `ItemIdentifier` against that item --
  including the catalogued name itself, since data-model.md asks that *every*
  string that has ever meant an item be a row, and the name a volunteer types
  most often is the one the catalogue shows them.
- **A string that resolves to nothing** becomes an `UnresolvedItemString`, and
  never an identifier. That model's own docstring argues the case.

The catalogue tab also holds a manufacturer part number per row, which would
make a second identifier each. Rule 1 does not read that column, so neither
does this: a `MFG_PART` identifier is a rule nobody has written yet.

## What it will not do

Rewrite anything. An identifier already naming a different item is somebody's
decision -- the admin has a page for these -- and an importer that overwrote it
would undo that curation silently on every re-run, so such a string is counted
and left alone. Nor does it delete: a catalogued name the export has dropped
still has stock behind it in the ledger. Only the review list is made equal to
the export, for the reason `UnresolvedItemString` gives.

## What it refuses, and why a refusal is a line rather than an exception

Three cells the export can hold cannot become the row they are for, and each is
a fault in a hand-maintained spreadsheet rather than anything this can settle.
Left to the database they are all one outcome -- a `psycopg` error part way
through, and a run that imported nothing -- so each is checked here, counted,
and printed by `section` beside the rows that did get written. The cell itself
is still readable in the staged rows, which is the same answer `_people.py`
gives about an address it declines to write.

- **Two catalogue rows spelling one name.** `Item.name` is case-sensitively
  unique, so `LiteBeam` beside `Litebeam` is two items; rule 1 is
  case-insensitive, so it can reach only the first, and the identifier table
  folds them together as well. The second item would end the import with no
  identifier at all -- unreachable by any scan or typed string, which is the
  one promise `ItemIdentifier` makes -- while accumulating stock through the
  ledger. So the tab is folded the way rule 1 reads it and the first spelling
  is the catalogued one, exactly as `resolve` would pick it.
- **A catalogue row longer than `Item.name`.** No item, and nothing resolves to
  it: a name the catalogue cannot hold is a name the pick-list cannot show.
- **An item string longer than `ItemIdentifier.value`.** One bound covers both
  places such a string would be written, which is why it is checked before
  rule 1 is asked rather than after: an over-long string is refused as an
  identifier by that column, and `UnresolvedItemString.value` is a `TextField`
  primary key, so a pasted block of prose exceeds PostgreSQL's btree key limit
  and cannot be recorded as a review-list entry either. A string too long to be
  an identifier is not an item string, and neither table is asked to hold it.
"""

from dataclasses import dataclass

from django.db import transaction

from inventory import identifiers
from inventory.models import Category, Item, ItemIdentifier
from inventory.sheet import Report, items
from inventory.sheet.workbook import Sheet
from inventory.staging import UnresolvedItemString

# The catalogue tab groups nothing: it is 52 rows of name, vendor, price and
# stock, with no column saying what kind of thing each one is. `Item.category`
# is not nullable -- the data model treats a category as part of what an item
# is -- so every imported item lands in one whose name says the grouping has
# not happened yet, rather than in a taxonomy this module invented from product
# names. Moving an item out of it is an ordinary admin edit, and the category
# empties as that work is done.
UNCATEGORISED = "Uncategorised"

# How long each of the two cells may be. These are `Item.name`'s and
# `ItemIdentifier.value`'s own lengths, and `test_mint_items` asserts they
# still are -- so a migration that widens a column and leaves these behind
# fails the build, rather than going on refusing rows the database would now
# take. Written out rather than read off `_meta`, which types as a field that
# may carry no length at all and would need two suppressions to say otherwise.
LONGEST_NAME = 150
LONGEST_VALUE = 200


def _kind(value: str) -> str:
    """Which kind of identifier a string is.

    Two of the five kinds can be told from the string itself and the other
    three cannot: a manufacturer part number and a vendor SKU are columns of
    the catalogue tab that no rule reads yet, and a barcode is printed by this
    system rather than found in a spreadsheet. So a retired NYCM code is one,
    and everything else is an alias -- which is what an item string typed into
    a form is, including the one that matches the catalogue exactly.
    """
    if items.RETIRED_CODE.match(value):
        return ItemIdentifier.Kind.LEGACY_NYCM
    return ItemIdentifier.Kind.ALIAS


@dataclass(frozen=True)
class Minted:
    """What one run of `mint` did, for the command to print.

    Each pair is a total and the part of it this run is responsible for, so
    that a second run over the same export reports the same totals with zeroes
    beside them -- which is what "idempotent" looks like as something a person
    checking the output can see.
    """

    items: int
    items_added: int
    # Catalogue rows that named no item of their own, both refusals the module
    # docstring argues: one whose name another row already spells but for its
    # case, and one whose name is longer than the column that would hold it.
    duplicated: int
    names_too_long: int
    # Distinct identifiers, so two casings of one string count once: that is
    # what the unique constraint makes of them.
    identifiers: int
    identifiers_added: int
    # Strings whose identifier already names some other item. Left alone, for
    # the reason the module docstring gives, and reported so that "no string
    # resolves to an item the rule said it does not name" is checkable.
    naming_another_item: int
    unresolved: int
    # Of those, the ones nobody wrote a reason for -- `How.UNACCOUNTED`, which
    # rule 1 says must never happen. Expected to be zero, and a finding when it
    # is not.
    unaccounted: int
    # Item strings neither table can hold, so rule 1 was never asked about
    # them. The third refusal the module docstring argues.
    strings_too_long: int


def section(minted: Minted) -> Report:
    """What the catalogue came to, and what the rule could make nothing of.

    Beside the counts rather than in a command, which `_staging.section`
    argues.
    """
    return "Catalogue", [
        ("catalogued items", minted.items),
        ("  this run added", minted.items_added),
        ("  duplicate rows in the catalogue tab", minted.duplicated),
        ("  catalogue rows too long to be a name", minted.names_too_long),
        ("identifiers", minted.identifiers),
        ("  this run added", minted.identifiers_added),
        ("  already naming another item", minted.naming_another_item),
        ("strings naming no catalogued item", minted.unresolved),
        ("  with no reason written", minted.unaccounted),
        ("strings too long to be an identifier", minted.strings_too_long),
    ]


@dataclass(frozen=True)
class Tab:
    """The catalogue tab as a list rule 1 can be asked to resolve against.

    Built before anything is written, because the two refusals it counts have
    to be settled before an `Item` exists: minting one and discovering
    afterwards that no identifier can name it is the state the whole check
    exists to prevent.
    """

    names: tuple[str, ...]
    duplicated: int
    too_long: int


def _tab(catalogue: tuple[str, ...]) -> Tab:
    """The catalogued names, less the rows that cannot become one.

    Order is the tab's own, and the spelling kept is the first of a folded
    pair, so that this and `items.resolve` -- which walks the catalogue and
    takes the first case-insensitive match -- always name the same item.
    """
    kept: dict[str, str] = {}
    duplicated = 0
    too_long = 0
    for name in catalogue:
        if len(name) > LONGEST_NAME:
            too_long += 1
        elif (key := identifiers.normalised(name)) in kept:
            duplicated += 1
        else:
            kept[key] = name
    return Tab(tuple(kept.values()), duplicated, too_long)


def folded(catalogue: tuple[str, ...]) -> tuple[str, ...]:
    """The names rule 1 is asked to resolve against, for every step that asks it.

    Public because the ledger step has to resolve against this list and not
    the tab: `items.resolve` takes an exact match before it folds, so a tab
    spelling one name twice answers that step with a name this one minted no
    `Item` for, and the run stops saying the catalogue step never ran.
    """
    return _tab(catalogue).names


def uncategorised() -> Category:
    """The category an imported row lands in, made by whichever run wants it first."""
    category, _ = Category.objects.get_or_create(name=UNCATEGORISED, parent=None)
    return category


def _catalogue(names: tuple[str, ...]) -> tuple[dict[str, Item], int]:
    """An `Item` per catalogued name, and how many of them were new."""
    category = uncategorised()
    catalogued: dict[str, Item] = {}
    added = 0
    for name in names:
        # get_or_create rather than bulk_create: these models keep history, and
        # bulk_create does not fire the signals that write it.
        catalogued[name], created = Item.objects.get_or_create(name=name, defaults={"category": category})
        added += created
    return catalogued, added


@transaction.atomic
def mint(sheet: Sheet) -> Minted:
    """Mint what rule 1 resolves, and write down what it does not.

    In one transaction because a half-minted catalogue would leave later steps
    resolving some strings and not others, with nothing saying which.
    """
    tab = _tab(sheet.catalogue)
    catalogued, items_added = _catalogue(tab.names)
    strings = {s.item for s in sheet.submissions if s.item}
    # Asked of rule 1 only where an answer could be written down. The bound is
    # the identifier column's, and it stands in for the review list's own limit
    # too, which the module docstring works through.
    too_long = {string for string in strings if len(string) > LONGEST_VALUE}
    resolutions = {string: items.resolve(string, tab.names) for string in strings - too_long}

    # Keyed by the normalised value because that is what the unique constraint
    # is keyed by: `LiteBeam` and `litebeam` are one identifier, and inserting
    # the second would raise rather than add a row.
    answering = {one.value_normalised: one for one in ItemIdentifier.objects.all()}
    # The catalogued names first, then the strings volunteers typed, so that a
    # string differing from a name only in case finds the name already there
    # and the spelling that survives is the one the catalogue shows. Sorted, so
    # that a run does not depend on the order a set happened to iterate in.
    naming = [(name, name) for name in tab.names]
    naming += sorted((string, answer.item) for string, answer in resolutions.items() if answer.item)

    answered: set[str] = set()
    added = 0
    elsewhere = 0
    for value, name in naming:
        key = identifiers.normalised(value)
        answered.add(key)
        item = catalogued[name]
        held = answering.get(key)
        if held is None:
            answering[key] = ItemIdentifier.objects.create(item=item, kind=_kind(value), value=value)
            added += 1
        # item_id rather than item, so that a row already in the table is not
        # re-fetched to compare a key it already holds. See DEVELOPERS.md#typing
        # for why the checker cannot see it.
        elif held.item_id != item.pk:  # ty: ignore[unresolved-attribute]
            elsewhere += 1

    unresolved = [
        UnresolvedItemString(value=string, reason=answer.why)
        for string, answer in sorted(resolutions.items())
        if not answer.item
    ]
    UnresolvedItemString.objects.exclude(value__in=[one.value for one in unresolved]).delete()
    UnresolvedItemString.objects.bulk_create(
        unresolved,
        update_conflicts=True,
        update_fields=["reason", "noted_at"],
        unique_fields=["value"],
    )
    return Minted(
        items=len(catalogued),
        items_added=items_added,
        duplicated=tab.duplicated,
        names_too_long=tab.too_long,
        identifiers=len(answered),
        identifiers_added=added,
        naming_another_item=elsewhere,
        unresolved=len(unresolved),
        unaccounted=sum(1 for one in unresolved if not one.reason),
        strings_too_long=len(too_long),
    )
