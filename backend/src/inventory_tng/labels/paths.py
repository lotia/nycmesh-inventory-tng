"""The labels' pages' paths, which other features' pages link to."""

from urllib.parse import urlencode

from inventory_tng.web.paths import ADMIN

LABELS = f"{ADMIN}/labels"
PRINT = f"{LABELS}/print"


def item_labels(item_id: int) -> str:
    """The labels list filtered to one item's labels and its packages',
    each offered to revoke (labels_routes._listed): current and inactive
    ones, but none revoked.

    Inactive ones are listed, as a retired package's label still fixes
    the item's unit (Ali, 2026-11-23, inventory-tng-x30g). Revoked ones
    are not, so they never push the others onto later pages (Ali,
    2026-11-24, inventory-tng-9t9y)."""
    query = {"item": item_id, "show": "all", "revoked": "no"}
    return f"{LABELS}?{urlencode(query)}"
