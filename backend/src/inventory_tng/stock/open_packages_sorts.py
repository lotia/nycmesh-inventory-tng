"""How lists of open packages (open_packages.Shown) sort by a heading: on
the reported used up and length check pages, and on an item's page
(catalogue/items_routes.py)."""

from inventory_tng.web.listing import Sort

# By their item, package and place.
BY_PACKAGE = Sort.keyed(
    lambda each: (each.item.lower(), each.package.lower(), each.place.lower())
)
# By what each shows left.
BY_SHOWS = Sort.keyed(lambda each: each.left)
# By when each was opened, the first first.
BY_OPENED = Sort.keyed(lambda each: each.row.opened_at)
# As open_packages.shown() lists them: the first reported used up, then the
# first opened.
OLDEST_FIRST = Sort.keyed(
    lambda each: (
        each.row.reported_at is None,
        each.row.reported_at or each.row.opened_at,
        each.row.opened_at,
        each.row.id,
    )
)
