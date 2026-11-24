"""The labels' pages' paths, which other features' pages link to."""

from urllib.parse import urlencode

from inventory_tng.web.paths import ADMIN

LABELS = f"{ADMIN}/labels"
PRINT = f"{LABELS}/print"


def item_labels(item_id: int) -> str:
    """The labels list filtered to one item's labels, its packages' too,
    each not revoked offered to revoke (labels_routes._listed). All of
    them, revoked ones too, as a retired package's label, inactive, still
    fixes the item's unit (Ali, 2026-11-23, inventory-tng-x30g)."""
    return f"{LABELS}?{urlencode({'item': item_id, 'show': 'all'})}"
