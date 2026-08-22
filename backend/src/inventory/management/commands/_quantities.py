"""Writing onto every imported item what the export's quantities were taken to mean.

The ledger step's sibling, and here rather than inside it for the reason
`_people.py` is a module of its own: this writes `Item.sheet_flag`, a column
that asks an administrator a question, and the ledger owns none of the others.
The leading underscore keeps it out of Django's command list, as `_ledger.py`
says of itself.

## The quantity is the one in the sheet, and the item is flagged

`StockMovement.quantity` is a decimal, so the number the form collected is
stored as it was written and nothing has to be narrowed. What is *not* done is
the thing section 5 of
[decision 0011](../../../../docs/decisions/0011-qr-batch-scanning.md)
leaves open: some volunteers wrote how many things they had and others how many
packets, and multiplying either by a guessed pack size would write a made-up
figure into a ledger nobody can edit.

Flagging the item is what is done instead, on `Item.sheet_flag` -- beside the
column `_people.py` writes on a volunteer, and there for the same reason. Every
item a run posts against is flagged, and a flag carries that item's own
quantities and how often each was written, so the ones needing no thought are
dismissed at a glance. Flagging fewer would mean deciding the size above which
a number is a packet, and that decision is the guess itself.

The census a flag carries is taken over every postable row of the export and
not over the rows one run happened to add. The sentence claims to list what
the sheet holds against that item, so a refreshed export whose three new rows
name it must not replace four hundred quantities with three.
"""

from collections import Counter
from collections.abc import Iterable
from decimal import Decimal

from inventory.models import Item

# How many of an item's quantities its flag lists before it says how many more
# there were. ToughCable was written seventy-odd ways; a flag nobody finishes
# reading is a flag nobody acts on.
QUANTITIES_SHOWN = 6


def _written(counted: Counter[Decimal]) -> str:
    """An item's quantities as a person reads them, commonest first.

    `normalize` because a decimal column hands back `1.000` and the sheet said
    `1`; the trailing zeroes are the column's precision and not something a
    volunteer wrote.
    """
    order = sorted(counted.items(), key=lambda pair: (-pair[1], pair[0]))
    shown = ", ".join(f"{quantity.normalize():f} x{count}" for quantity, count in order[:QUANTITIES_SHOWN])
    rest = len(order) - QUANTITIES_SHOWN
    return f"{shown}, and {rest} further quantities" if rest > 0 else shown


def _quantity_flag(counted: Counter[Decimal]) -> str:
    return (
        "The import took every quantity the sheet recorded against this item at face value: "
        f"{_written(counted)}. Whether a volunteer meant that many of the thing or that many "
        "packets of it is nowhere in the export, so nothing was multiplied by anything. Settle "
        "what one packet of this item is, put that on the labels you print for it, and clear "
        "this field."
    )


def flag(census: Iterable[tuple[Item, Decimal]], posted_against: Iterable[Item]) -> int:
    """Write onto every item a run posted against what the export took literally.

    The census is the export's and the items written to are the run's, which
    is what the module docstring argues each of separately: a flag has to
    describe every row the sheet holds for that item, and flagging only what
    the run posted is what keeps a second run from undoing an administrator's
    work. A run that posts nothing therefore flags nothing, so a flag somebody
    has cleared stays cleared.

    `save` rather than an update, because an item keeps its history and a flag
    appearing on a row is a change to it.
    """
    counted: dict[Item, Counter[Decimal]] = {}
    for item, quantity in census:
        counted.setdefault(item, Counter())[quantity] += 1
    # A dict rather than a set, so two runs over one export write the flags in
    # one order and the histories they leave read the same way.
    flagging = dict.fromkeys(posted_against)
    for item in flagging:
        item.sheet_flag = _quantity_flag(counted[item])
        item.save(update_fields=["sheet_flag"])
    return len(flagging)
