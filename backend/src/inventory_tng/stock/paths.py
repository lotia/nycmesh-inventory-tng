"""The stock pages' paths, which other features' pages link to."""

from inventory_tng.catalogue.paths import ITEMS
from inventory_tng.web.paths import ADMIN

STOCK = f"{ADMIN}/stock"
REORDER = f"{STOCK}/reorder"
NEEDS_COUNTING = f"{STOCK}/needs-counting"

# Lines recorded with more than was moved (stock/over_recorded_routes.py):
# those waiting, and those settled.
OVER_RECORDED = f"{STOCK}/recorded-more"
OVER_RECORDED_SETTLED = f"{OVER_RECORDED}/settled"

LENGTHS = f"{STOCK}/length-checks"
USED_UP = f"{STOCK}/used-up"
CLOSE = f"{ITEMS}/{{item_id}}/open-packages/{{open_id}}/close"

COUNTS = f"{ADMIN}/counts"
ADJUSTMENTS = f"{COUNTS}/adjustments"
