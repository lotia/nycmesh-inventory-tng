"""The labels' pages' paths, which other features' pages link to."""

from urllib.parse import urlencode

from inventory_tng.web.paths import ADMIN

LABELS = f"{ADMIN}/labels"
PRINT = f"{LABELS}/print"


def item_labels(item_id: int) -> str:
    """The labels list filtered to one item's labels, its packages' too,
    each offered to revoke (labels_routes._listed)."""
    return f"{LABELS}?{urlencode({'item': item_id})}"
