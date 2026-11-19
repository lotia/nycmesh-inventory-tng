"""The administrator pages' navigation: its sections, in order, and their
badges, each listed by name. A page drawn for an administrator shows it
(web/pages.py's page)."""

from collections.abc import Callable
from typing import NamedTuple

from babel.support import LazyProxy
from sqlalchemy.orm import Session, object_session

from inventory_tng.admins.paths import ADMINS
from inventory_tng.catalogue.paths import CATEGORIES, ITEMS
from inventory_tng.core.phrases import lazy_gettext, ngettext
from inventory_tng.devices.paths import DEVICES
from inventory_tng.labels.paths import LABELS
from inventory_tng.places.paths import LOCATIONS
from inventory_tng.site_settings.paths import SETTINGS
from inventory_tng.stock.balance import retired_holding_stock_count
from inventory_tng.stock.paths import COUNTS, STOCK
from inventory_tng.volunteers.paths import VOLUNTEERS

# The sections' names: in the navigation, in links back to them, and as
# their pages' titles.
SECTION_ITEMS = lazy_gettext("Items")
SECTION_CATEGORIES = lazy_gettext("Categories")
SECTION_LOCATIONS = lazy_gettext("Locations")
SECTION_LABELS = lazy_gettext("Labels")
SECTION_VOLUNTEERS = lazy_gettext("Volunteers")
SECTION_STOCK = lazy_gettext("Stock")
SECTION_COUNTS = lazy_gettext("Counts")
SECTION_DEVICES = lazy_gettext("Devices")
SECTION_ADMINS = lazy_gettext("Administrators")
SECTION_SETTINGS = lazy_gettext("Site settings")


def retired_holding(places: int) -> str:
    """How many retired places hold stock, in words."""
    return ngettext(
        "%(num)s retired place holds stock",
        "%(num)s retired places hold stock",
        places,
    )


class Badge(NamedTuple):
    """A number beside a section in the navigation: how many things to put
    right there, and what they are, for a screen reader."""

    count: int
    words: str


def counts_badge(session: Session) -> Badge | None:
    """Beside Counts: how many retired places hold stock, to count
    (balance.retired_holding_stock)."""
    many = retired_holding_stock_count(session)
    return Badge(many, retired_holding(many)) if many else None


# The administrator pages' sections, in the navigation's order, by path and
# name, and the badge of any that has one, by path.
SECTIONS: tuple[tuple[str, str | LazyProxy], ...] = (
    (ITEMS, SECTION_ITEMS),
    (CATEGORIES, SECTION_CATEGORIES),
    (LOCATIONS, SECTION_LOCATIONS),
    (LABELS, SECTION_LABELS),
    (VOLUNTEERS, SECTION_VOLUNTEERS),
    (STOCK, SECTION_STOCK),
    (COUNTS, SECTION_COUNTS),
    (DEVICES, SECTION_DEVICES),
    (ADMINS, SECTION_ADMINS),
    (SETTINGS, SECTION_SETTINGS),
)
BADGES: dict[str, Callable[[Session], Badge | None]] = {COUNTS: counts_badge}


def sections(admin: object) -> list[tuple[str, str | LazyProxy, Badge | None]]:
    """The navigation, for a page drawn for `admin`: each section with its
    badge, read in the session that loaded `admin`. With none open, no
    badges."""
    session = object_session(admin)

    def badge(path: str) -> Badge | None:
        return BADGES[path](session) if session and path in BADGES else None

    return [(path, label, badge(path)) for path, label in SECTIONS]
