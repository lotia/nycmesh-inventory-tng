"""The catalogue's pages' paths, which other features' pages link to."""

from urllib.parse import urlencode

from inventory_tng.web.paths import ADMIN

ITEMS = f"{ADMIN}/items"
CATEGORIES = f"{ADMIN}/categories"
READINGS = f"{ADMIN}/unit-readings"
SEED = f"{READINGS}/seed"
# An item's guided recount into another unit (catalogue/unit_recounts.py),
# and the page after it.
UNIT_RECOUNT = f"{ITEMS}/{{item_id}}/unit-recount"
UNIT_RECOUNTED = f"{ITEMS}/{{item_id}}/unit-recounted"


def unit_recount(item_id: int, unit: str) -> str:
    """The guided recount of the item into `unit`."""
    return f"{UNIT_RECOUNT.format(item_id=item_id)}?{urlencode({'unit': unit})}"
