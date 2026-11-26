"""A place's count form: the page with which an administrator counts a
place, and the areas in it, to put the records right
(docs/decisions/0003), reached from Counts (stock/counts_routes.py). The writing is
stock/store_counts.py's (record_store_count); this page checks what was
typed and says what is wrong.

The form carries a key made when it was shown, so sending it twice writes
it once. Saved, then sent again under that key saying anything otherwise
(other figures, an open package found otherwise or closed, a place left
out before now typed at, a move's amount fixed), it writes nothing and
goes back to Counts saying it was saved already (Ali, 2026-11-22,
inventory-tng-53im, inventory-tng-kro9). A count also carries the place's
latest ledger entry as it was shown, and how many there it saw
(moments.Moment): the count is true for that moment, so a save made at the
place meanwhile still applies, and an item it moved comes back to count
again. The moment is read without the place's lock, so showing the form
holds up no save there; a save under way as it was shown, committed after
with an id inside the moment, refuses the count (changed_since_shown). Which
items that save moved cannot be told, so the form is shown again as it is
now, keeping what was typed, to be checked and sent again.

A count also carries the unit of each item and the size of each package
it showed (forms.UNITS): one changed since, even between the page's
checks and the write, is refused, and the form shown again in the units
and sizes now, each box of one changed saying so, nothing written
(stock/units_shown.py; Ali, 2026-11-23, inventory-tng-57u8). A place
retired meanwhile the ledger refuses (location_countable) unless the form
was written there before.

A count names each item once: a form naming one on two fields (counted_5
and counted_05, or the select at the foot choosing an item listed above) is
refused and shown again saying why, as it isn't clear which number is
right. The page itself never sends one. The ledger refuses a repeat too
(movement_item_once), but the page checks first, to say which fields.
Something else here naming an item the place picked for it lists already,
which a store's form offers, is refused too, saying it is listed there and
to type it on its line (Ali, 2026-11-21, inventory-tng-fh0e): on a count of
one place only a hand-made form sends that, and it is said the same way. On
a recount, naming an item the place held but the recount does not ask for
is refused so too, and the form shown again adds that item's line there
(Ali, 2026-11-22, inventory-tng-oazl).

A place's count can show only some items: one (`?item=`, as the pages of
what needs an administrator's action take it), those whose names match a
search (`?q=`, as the items list searches), or both. Only those are
counted; the rest stay as they are, as any item left uncounted does. The
filter rides on the form's action, so a form shown again keeps it.

A place with areas in it is counted with every area inside it, at any
depth, on one form, grouped by area, one count at each place, as decision
0003's 2026-11-21 amendment says (inventory-tng-8i4.51.4). The form lists
the places it shows (count_forms.PLACES); its moment is read across them all,
and the send holds them all before writing any (stock/store_counts.py).
An area retired and empty is left out. Filtered to some items, an area
with nothing typed is not counted; with nothing typed at any place,
nothing is, and the form is shown again saying so (Ali, 2026-11-21,
inventory-tng-fxsy).

An area on the form that is retired, or a retired one emptied, while it
is counted may no longer be counted: the save is refused whole, and the
form shown again without it, keeping what was typed (Ali, 2026-11-22,
inventory-tng-ygk5). The form then carries the areas gone from it and
the moment it was first shown (count_forms.GONE), so it shows, until saved,
what left them since (stock/count_gone_areas.py): what went to a place on
it is set aside from what that place's own lines count, and each move is
taken as right unless its amount is fixed, in each form it arrived in,
the difference then lost or found there (stock/count_arrivals.py). One
emptied can be brought back from the form (bring_back), as its own page
does (places/locations.restore), and the form shown again with it,
keeping what was typed, a box of an item or package shown otherwise
since saying so as a count sent does.
"""

import uuid
from collections.abc import Callable, Collection, Mapping, Sequence
from decimal import Decimal
from functools import cache, partial
from typing import Annotated, NamedTuple
from urllib.parse import urlencode

import structlog
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response
from opentelemetry import metrics
from sqlalchemy.orm import Session

from inventory_tng.admins.guards import AdminSignedIn, SignedIn
from inventory_tng.catalogue.items_listing import SEARCHED
from inventory_tng.core.db_errors import Gone as RowGone
from inventory_tng.core.db_errors import attempt, refusal
from inventory_tng.core.db_sessions import DbSession, SnapshotSession, by_name
from inventory_tng.core.phrases import Words, gettext, lazy_gettext, ngettext
from inventory_tng.core.sent_forms import form_key
from inventory_tng.models.base import INTEGER_MAX, whole
from inventory_tng.models.catalogue import Item, ItemPackage
from inventory_tng.models.places import Location
from inventory_tng.places import locations
from inventory_tng.stock import needs_action, over_recorded, queries
from inventory_tng.stock.count_areas_leaving import (
    areas_leaving,
    as_shown,
    moves_since,
)
from inventory_tng.stock.count_arrivals import (
    Arrivals,
    Aside,
    Fix,
    arrived_at,
    as_held,
    fixed,
    fixed_lines,
    held_aside,
    less,
)
from inventory_tng.stock.count_asked_for import asks_for, forms_by_place
from inventory_tng.stock.count_forms import (
    CLOSED_MEANWHILE,
    COUNTED,
    EXTRA_ITEM,
    EXTRA_PLACE,
    FOUND_PLACE,
    LOOSE_FIELD,
    NONE_GONE,
    PICKED,
    TYPE_IT_HERE,
    Figures,
    Gone,
    Listed,
    field_at,
    fields_at,
    gone_listed,
    item_counts,
    places_listed,
    read_count,
)
from inventory_tng.stock.count_gone_areas import gone_words, moved_sections
from inventory_tng.stock.count_groups import Group
from inventory_tng.stock.count_units_shown import (
    asked_again,
    kept_as_now,
)
from inventory_tng.stock.counts import COUNTABLE, WHOLE, NotCountable
from inventory_tng.stock.forms import shown_field, shown_in
from inventory_tng.stock.ledger import by_item, tracks_opened
from inventory_tng.stock.moments import (
    Moment,
    expected_at,
    moment,
    seen_up_to,
)
from inventory_tng.stock.moved_out import MovedOut, moved_out
from inventory_tng.stock.needs_action import OnlyItem
from inventory_tng.stock.paths import COUNTS
from inventory_tng.stock.queries import PlaceTree
from inventory_tng.stock.store_counts import (
    StoreCounted,
    record_store_count,
)
from inventory_tng.stock.units_shown import (
    SHOWN_OTHERWISE,
    Now,
    ShownOtherwise,
)
from inventory_tng.web.admin_pages import TextForm, admin_page, now
from inventory_tng.web.decimal_separator import decimal_separator
from inventory_tng.web.forms import CHANGED, RULES, row_id
from inventory_tng.web.navigation import SECTION_COUNTS
from inventory_tng.web.pages import add_notices, not_found, redirect

log = structlog.get_logger(__name__)
meter = metrics.get_meter(__name__)

router = APIRouter(include_in_schema=False)

# An open package found that its item does not follow once opened, and a
# form counted twice: said as rules are.
UNTRACKED = "open_package_untracked"
ONCE = "movement_item_once"
# A form saved, sent again saying otherwise (stock/store_counts.py):
# nothing is written, and Counts says so (Ali, 2026-11-22,
# inventory-tng-53im, inventory-tng-kro9).
SENT_AGAIN = "count_sent_otherwise"
TWICE = lazy_gettext(
    "An item is on this form more than once. Each item is counted once, so "
    "it isn't clear which number is right: nothing was saved."
)
# A count refused as a save under way when its form was shown committed
# after (counts.record_count): what was typed is kept.
SAVED_AS_SHOWN = lazy_gettext(
    "Not saved: something here was saved just as you opened this count, so "
    "the records may say otherwise now. What you typed is kept: check it "
    "and save again."
)
# A filtered count of a store and its areas sent with nothing typed at
# any place.
NOTHING_TYPED = lazy_gettext("Nothing was typed, so nothing was counted.")
# A count of a store and its areas, saved (inventory-tng-8i4.51.4).
places_counted = meter.create_histogram(
    "inventory.count.places",
    unit="{place}",
    description="Places each count form saved counted: one, or a store or "
    "area and the areas in it",
)
# A retired area emptied while it was counted, brought back from the count
# form (inventory-tng-ygk5).
areas_brought_back = meter.create_counter(
    "inventory.count.areas_brought_back",
    unit="{area}",
    description="Retired areas emptied while they were counted, brought back "
    "from the count form",
)

# A move from an area gone from a count form fixed on it: more or less
# arrived than was moved, in any of the forms it arrived in
# (inventory-tng-ygk5).
moves_fixed = meter.create_counter(
    "inventory.count.moves_fixed",
    unit="{move}",
    description="Moves from retired areas emptied while counted whose amount "
    "a count form fixed, by whether more arrived (found) or less (lost)",
)


def brought_back(place: str) -> str:
    """An area gone from a count form, brought back from it."""
    # Translators: %(place)s is a place of any kind, such as a store, a
    # shelf, a bin or a volunteer's home.
    return gettext(
        "%(place)s is back, and what you typed is kept. Add what's at %(place)s "
        "with Something else here, then save the count.",
        place=place,
    )


def already_listed(item: str, place: str) -> str:
    """A count refused as something else here names an item the place
    picked for it lists (count_forms.Listed): what was typed is kept."""
    # Translators: %(place)s is a place of any kind, such as a store, a
    # shelf, a bin or a volunteer's home.
    return gettext(
        "%(item)s is already listed under %(place)s. Type what you found on "
        "its line there. Nothing was saved, and what you typed is kept.",
        item=item,
        place=place,
    )


def listed_under(place: str) -> str:
    """Beside something else here's item, listed at the place picked."""
    # Translators: %(place)s is a place of any kind, such as a store, a shelf,
    # a bin or a volunteer's home.
    return gettext("Listed under %(place)s already.", place=place)


def count_title(place: str) -> str:
    # Translators: %(place)s is a place of any kind, such as a store, a shelf,
    # a bin or a volunteer's home.
    return gettext("Count %(place)s", place=place)


def moved_meanwhile(saved: int) -> str:
    """A count refused as some of it moved while it was counted, the lines
    saved besides, if any."""
    if not saved:
        return gettext("These moved while you counted, so count them again.")
    return ngettext(
        "Saved %(num)s line. These moved while you counted, so count them again.",
        "Saved %(num)s lines. These moved while you counted, so count them again.",
        saved,
    )


COUNTED_NOTICE = lazy_gettext("Counted. The records now match what you found.")
COUNTED_SAME = lazy_gettext("Counted: everything was as the records say.")
SAVED_ALREADY = lazy_gettext(
    "This count was saved already, so what you changed wasn't saved. To "
    "record it, start a new count."
)
add_notices(
    {
        "counted": COUNTED_NOTICE,
        "counted_same": COUNTED_SAME,
        "count_saved_already": SAVED_ALREADY,
    }
)


class Shown(NamedTuple):
    """Which items a place's count shows (module docstring): one, those
    matching a search, or both; neither for all."""

    only: Item | None = None
    search: str = ""

    def ids(self, session: Session) -> set[int] | None:
        """The items shown, of any at all; None for every one."""
        if not self.search:
            return None if self.only is None else {self.only.id}
        return queries.item_ids_matching(
            session,
            SEARCHED.matching(self.search, SEARCHED.rules(session)),
            None if self.only is None else self.only.id,
        )

    def params(self) -> dict[str, str]:
        """The query that shows the count so."""
        return needs_action.query(self.only) | (
            {"q": self.search} if self.search else {}
        )

    def query(self) -> str:
        """The query string that shows the count so, for links and the
        form's action."""
        query = self.params()
        return f"?{urlencode(query)}" if query else ""


def shown_items(only: OnlyItem, q: str = "") -> Shown:
    """The filter a count's query string asks for: an odd search is none,
    as the items list reads it."""
    return Shown(only, SEARCHED.read(q=q).search)


ShownItems = Annotated[Shown, Depends(shown_items)]


def _shown_places(
    session: Session,
    tree: PlaceTree,
    places: Collection[int] | None,
) -> list[tuple[Location, tuple[str, ...]]]:
    """The places a count form shows, each with its path, of its place's
    `tree` (queries.place_tree): the place and every area in it that may be
    counted, as they are now; or of those, the places `places` names, in
    that order, then any not in it now (an area moved out of it since),
    under its own name."""
    countable = [(place, path) for place, path, may in tree if may]
    if places is None:
        return countable
    shown = [(place, path) for place, path in countable if place.id in places]
    gone = set(places) - {place.id for place, _ in shown}
    for place in by_name(session, Location, Location.id.in_(gone)):
        shown.append((place, (place.name,)))
    return shown


def _listed_words(
    session: Session, tree: PlaceTree, listed: Listed
) -> tuple[Words, dict[str, Words]]:
    """What a count form of the place of `tree` (queries.place_tree) says
    of something else here naming an item the place picked for it lists
    (count_forms.Listed): above the form, and beside the item picked. A
    recount's line added for it is marked as the form shown again shows it
    (_count_page's `off_form`)."""
    item = session.get_one(Item, listed.item_id)
    ((place, path),) = _shown_places(session, tree, [listed.place_id])
    where = queries.place_label(place, path)
    return already_listed(item.name_shown, where), {EXTRA_ITEM: listed_under(where)}


def _count_page(
    request: Request,
    session: Session,
    signed: SignedIn,
    place: Location,
    at: Moment | None,
    *,
    shown: Shown,
    places: Collection[int] | None = None,
    problems: Mapping[str, Words] | None = None,
    values: Mapping[str, str] | None = None,
    items: Mapping[int, Collection[int]] | None = None,
    recount: Mapping[int, Mapping[int, Figures]] | None = None,
    alert: Words | None = None,
    picked: Mapping[str, str] | None = None,
    gone: Gone = NONE_GONE,
    gone_now: Collection[int] = (),
    tree: PlaceTree | None = None,
    left: list[MovedOut] | None = None,
    off_form: Listed | None = None,
    caution: Words | None = None,
    status_code: int | None = None,
) -> HTMLResponse:
    """The count form of a place and the areas in it, with the records as
    of `at`, or now if None, read across the places it shows. Of everything
    there, or a recount of `items` only, by place, which the form carries.
    With `recount`, by place, a recount of its items: what was typed for
    each and what came in and went out while it was counted. Of those, only
    the items `shown`, but a recount's every one. `places`, the places the
    form showed when sent; else every one that may be counted now.
    `picked`, the places picked for something else here and an open
    package nobody recorded at the last save (count_forms.PICKED). `gone`,
    the areas gone from it, retired, and what left them since it was first
    shown, up to its moment, shown under the places it went to
    (count_gone_areas), whose own lines count what they held before
    (count_arrivals.as_held); `gone_now`, those of them just gone, said
    in alerts in place of `alert` (gone_words); `tree`, the place's
    (queries.place_tree), and `left`, what left the areas gone, split at
    `at` (moves_since), if read already. `off_form`, something else here
    naming an item a recount did not ask for at its place
    (count_forms.Listed), whose line the form now shows there, marked;
    `caution`, words above the form, said as a warning is. Answered with
    `status_code`, else 400 with `problems` and 200 without. What it shows
    at each place is _place_lines'."""
    # Only places with figures: a form with none has no While you counted.
    recount = {place_id: each for place_id, each in (recount or {}).items() if each}
    asked: dict[int, set[int]] = {}
    for place_id, each in [*(items or {}).items(), *recount.items()]:
        if each:
            asked.setdefault(place_id, set()).update(each)
    ids = shown.ids(session)
    # The place's tree, read once for the places shown and the areas gone.
    if tree is None:
        tree = queries.place_tree(session, place)
    groups_shown = _shown_places(
        session, tree, places if places is not None else asked or None
    )
    ids_shown = [each.id for each, _ in groups_shown]
    at = at or moment(session, *ids_shown)
    if left is None:
        left = moves_since(session, gone, at.as_of)
    leaving = areas_leaving(session, gone, tree, left) if gone.ids else None
    more_alerts: Sequence[Words] = ()
    if gone_now:
        assert leaving is not None  # The areas just gone are of those gone.
        alert, *more_alerts = gone_words(leaving, gone_now)
    # Of what left the areas gone, only the items shown; each place's own
    # lines count what it held before.
    moves = [
        move for move in as_shown(left, ids_shown) if ids is None or move.item_id in ids
    ]
    listed = queries.countable_items(session)
    arrivals = as_held(session, gone, at.as_of, moves)
    groups, named, packages = _place_lines(
        session,
        place,
        groups_shown,
        at.as_of,
        asked,
        recount,
        shown.only,
        ids,
        moves,
        arrivals,
        listed,
    )
    if ids is not None:
        listed = [item for item in listed if item.id in ids]
    moved = None
    if leaving is not None:
        moved = moved_sections(
            session,
            place,
            groups,
            leaving,
            moves,
            named,
            packages,
            arrivals,
            gone.since,
            at.as_of,
        )
    if off_form is not None:
        # Its line there, as shown: by its total, or by its forms, loose.
        group = next(group for group in groups if group.place.id == off_form.place_id)
        by_form = off_form.item_id in group.forms
        line = f"{group.prefix}{LOOSE_FIELD if by_form else COUNTED}{off_form.item_id}"
        problems = {**(problems or {}), line: TYPE_IT_HERE}
    # Something else here and an open package nobody recorded may be found
    # in any place shown but a retired area (count_forms.PICKED).
    choices = [
        group
        for group in groups
        if group.place.retired_at is None or group.place.id == place.id
    ]
    on_lines = [{item.id for item, _ in group.rows} for group in choices]
    found_packages = [
        found for found in _followed(session) if ids is None or found[0].item_id in ids
    ]
    others = [
        item
        for item in listed
        if not all(item.id in here for here in on_lines)
        or str(item.id) == (values or {}).get(EXTRA_ITEM)
    ]
    # The unit of every item it shows a box of an amount of, and the size of
    # every package of them, as it shows them now (forms.UNITS).
    units = {item.id: item.unit for item in [*named.values(), *others]}
    units |= {package.item_id: unit for package, _, unit in found_packages}
    return admin_page(
        request,
        signed,
        "stock/count.html",
        {
            "title": count_title(place.name),
            "here": f"{COUNTS}/{place.id}",
            "back_link": (COUNTS, SECTION_COUNTS),
            "place": place,
            "groups": groups,
            "areas": any(group.place.id != place.id for group in groups),
            "choices": choices,
            "picked": {
                picker: (values or {}).get(picker) or (picked or {}).get(picker, "")
                for picker in PICKED
            },
            "found_packages": found_packages,
            "recount": recount,
            # What a place it may be found in does not list already, and the
            # item picked, to keep it picked on a form shown again: a
            # recount adds its line (count_forms.read_count).
            "others": others,
            "units": shown_field(units),
            "sizes": shown_field(
                {
                    package_id: package.size_in_items_unit
                    for package_id, package in packages.items()
                }
            ),
            "key": (values or {}).get("key") or uuid.uuid4(),
            "moment": at,
            "shown": shown,
            "alert": alert,
            "more_alerts": more_alerts,
            "caution": caution,
            "gone": gone,
            "moved": moved,
            "problems": problems,
            "values": values,
        },
        status_code or (400 if problems else 200),
    )


def _place_lines(
    session: Session,
    place: Location,
    shown_places: Sequence[tuple[Location, tuple[str, ...]]],
    as_of: int,
    asked: Mapping[int, Collection[int]],
    recount: Mapping[int, Mapping[int, Figures]],
    only: Item | None,
    ids: Collection[int] | None,
    moves: Sequence[MovedOut],
    arrivals: Mapping[int, Arrivals],
    items: Sequence[Item],
) -> tuple[list[Group], dict[int, Item], dict[int, ItemPackage]]:
    """What a count form of `place` shows at each of `shown_places`, as of
    moment `as_of`, a group of lines at each; and every item they name, by
    id, of `items`, those that may be counted, and of the rest. Everything
    there, or a recount of `asked` only, by place, with each one's
    `recount` figures; of the items of `ids` only, None for all, and
    `only`, asked for, at the page's own place if none is at any place
    shown; less what `moves` brought there from the areas gone from it,
    as each place holds it (`arrivals`, count_arrivals.as_held). And
    every package of the items on its lines and of `moves`, by id, read
    once for both."""
    ids_shown = [each.id for each, _ in shown_places]
    every = set().union(*asked.values()) if asked else None
    was_at = expected_at(session, ids_shown, as_of, every)
    aside = held_aside(arrivals)
    for place_id, here in aside.items():
        if place_id in was_at:
            was_at[place_id] = less(was_at[place_id], by_item(here))
    named = {item.id: item for item in items}
    openings = over_recorded.openings_waiting_at(session, ids_shown, as_of)
    was: dict[int, dict[int, Decimal]] = {}
    for each in ids_shown:
        here = was_at.get(each, {})
        if asked:
            # A recount's items were shown when counted: each is asked for
            # again, though the search may no longer find it.
            here = {
                item_id: here.get(item_id, Decimal(0))
                for item_id in asked.get(each, ())
            }
        else:
            # An opening recorded more than opened, waiting here: its item,
            # to count, though the records say none is here.
            for item_id in openings.get(each, {}):
                here.setdefault(item_id, Decimal(0))
            if ids is not None:
                here = {item_id: n for item_id, n in here.items() if item_id in ids}
        was[each] = here
    if (
        not asked
        and only is not None
        and only.id in named
        and (ids is None or only.id in ids)
        and place.id in was
        and not any(only.id in here for here in was.values())
    ):
        # The item asked for, to count, though the records say none is at
        # any place shown: at the page's own place.
        was[place.id] = {**was[place.id], only.id: Decimal(0)}
    moved_ids = {move.item_id for move in moves}
    if missing := set().union(*was.values(), moved_ids) - named.keys():
        # What the records had then but cannot be counted now, or what the
        # form's items, sent back, name that is no item at all.
        missed = queries.items_with_ids(session, missing)
        named |= {item.id: item for item in missed}
    rows = {
        each: sorted(
            (
                (named[item_id], amount)
                for item_id, amount in here.items()
                if item_id in named
            ),
            key=lambda row: row[0].name.lower(),
        )
        for each, here in was.items()
    }
    on_lines = {each: [item.id for item, _ in found] for each, found in rows.items()}
    packages = queries.packages_by_id(
        session, set().union(*on_lines.values(), moved_ids)
    )
    forms = forms_by_place(session, as_of, on_lines, openings, aside, packages)
    groups = [
        Group(
            each,
            path,
            field_at(place.id, each.id, ""),
            rows[each.id],
            forms.get(each.id, {}),
            sorted(asked.get(each.id, ())),
            recount.get(each.id),
        )
        for each, path in shown_places
    ]
    return groups, named, packages


@router.get(f"{COUNTS}/{{location_id:int}}")
def count_form(
    request: Request,
    signed: AdminSignedIn,
    session: SnapshotSession,
    location_id: int,
    shown: ShownItems,
    extra_location_id: str = "",
    found_location_id: str = "",
) -> Response:
    # One snapshot: the form's moment and every figure it shows, so it saw
    # each save there up to its moment that it shows, and a count is
    # refused only for one it did not (counts.record_count). The places
    # picked at the last save come back picked (count_forms.PICKED).
    place = queries.countable_place(session, location_id)
    if place is None:
        return not_found()
    picked = {EXTRA_PLACE: extra_location_id, FOUND_PLACE: found_location_id}
    return _count_page(
        request, session, signed, place, None, shown=shown, picked=picked
    )


def _followed(session: Session) -> list[tuple[ItemPackage, str, str]]:
    """Current packages of current items followed once opened, with their
    item's name and unit: what an open package nobody recorded may be."""
    return [
        (package, name, unit)
        for package, name, unit, item_tracks in queries.current_packages(session)
        if tracks_opened(item_tracks, package.track_opened)
    ]


class Sent(NamedTuple):
    """What every count form carries, as sent: its place, key and moment,
    the places it showed, and the areas gone from it (count_forms.Gone)."""

    place: Location
    key: uuid.UUID
    at: Moment
    place_ids: list[int]
    gone: Gone


def _sent(session: Session, form: Mapping[str, str], location_id: int) -> Sent | None:
    """What a count form of the place carries, or None if any of it is
    not as the form sends it, or names a place there is not."""
    key = form_key(form.get("key", ""))
    place = session.get(Location, location_id)
    as_of = whole(form.get("as_of", ""), 0, INTEGER_MAX)
    seen = whole(form.get("seen", ""), 0, INTEGER_MAX)
    place_ids = places_listed(form, location_id)
    gone = gone_listed(form)
    if (
        place is None
        or key is None
        or as_of is None
        or seen is None
        or place_ids is None
        or gone is None
        or len(queries.places_with_ids(session, place_ids)) != len(place_ids)
    ):
        return None
    return Sent(place, key, Moment(as_of, seen), place_ids, gone)


class SinceShown(NamedTuple):
    """What left a sent form's gone areas since it was shown, each read
    when first needed, once: all of it (`left`, moves_since); what the
    form showed of it (`shown`); what each place holds of that (`held`,
    count_arrivals.as_held), so what its lines are less (`aside`); and
    whether a full count of a place asks for an item, less that
    (`asks_for`)."""

    left: Callable[[], list[MovedOut]]
    shown: Callable[[], list[MovedOut]]
    held: Callable[[], dict[int, Arrivals]]
    aside: Callable[[], dict[int, Aside]]
    asks_for: Callable[[int, int], bool]


def _since_shown(session: Session, sent: Sent) -> SinceShown:
    """SinceShown of the form `sent`, as count and bring_back read it."""
    gone, as_of = sent.gone, sent.at.as_of
    left = cache(partial(moves_since, session, gone, as_of))
    shown = cache(lambda: as_shown(left(), sent.place_ids))
    held = cache(lambda: as_held(session, gone, as_of, shown()))
    aside = cache(lambda: held_aside(held()))
    asked = partial(asks_for, session, as_of, aside)
    return SinceShown(left, shown, held, aside, asked)


@router.post(f"{COUNTS}/{{location_id:int}}")
def count(
    request: Request,
    signed: AdminSignedIn,
    session: DbSession,
    location_id: int,
    form: TextForm,
    shown: ShownItems,
) -> Response:
    carried = _sent(session, form, location_id)
    if carried is None:
        return not_found()
    place, key, at, place_ids, gone = carried
    areas = place_ids != [place.id]
    # What came from the areas gone from it, as the form showed it: set
    # aside from what each place's lines count, so from what a full count
    # asks for, and the moves fixed (stock/count_arrivals.py). Read when
    # first needed, so a form sent back for a problem reads them only to
    # show them, unless something else here names a line it does not show.
    since = _since_shown(session, carried)
    left = since.left
    sent = read_count(
        session,
        form,
        place.id,
        place_ids,
        since.asks_for,
        separator=decimal_separator(request),
    )
    asked = {place_id: each.items for place_id, each in sent.places.items()}
    # The form shown again: as sent, or with `at` None, as it is now; a
    # recount with the figures it first showed (count_forms.WHILE).
    figures = {place_id: each.recount for place_id, each in sent.places.items()}
    again = partial(
        _count_page, request, session, signed, place, shown=shown, gone=gone
    )
    # Asking again for what it asked, with the figures it showed.
    asking = partial(again, items=asked, recount=figures)
    # The form as sent, as of when it was shown, of the places it showed.
    as_sent = partial(asking, at, places=place_ids, values=form)
    # What was typed, kept on a form shown as it is now, with a new key.
    typed = {name: value for name, value in form.items() if name != "key"}
    if place.id not in place_ids and not any(asked.values()):
        # Only a recount of some areas leaves its own place off.
        return not_found()
    if sent.twice:
        log.info(
            "count refused: an item named twice",
            location_id=place.id,
            item_ids=sorted({item_id for item_id in sent.twice.values() if item_id}),
        )
    problems: dict[str, Words] = dict(sent.problems)
    alert = TWICE if sent.twice else problems.get(CLOSED_MEANWHILE)
    # Shown in a unit or a package size changed since: asked again in the
    # new ones (units_shown), checked again as it is saved. A form saved
    # before is answered as it was (store_counts.record_store_count).
    shown_as = shown_in(form)
    words, said = kept_as_now(
        session,
        form,
        key,
        sent.places.values(),
        place.id,
        place_ids,
        separator=decimal_separator(request),
        refused=True,
        others=sent.problems,
    )
    problems |= words
    alert = alert or said
    tree = None
    off_form = None
    if sent.listed is not None:
        log.info(
            "count refused: something else here listed there",
            location_id=place.id,
            found_location_id=sent.listed.place_id,
            item_id=sent.listed.item_id,
        )
        tree = queries.place_tree(session, place)
        listed_alert, beside = _listed_words(session, tree, sent.listed)
        problems |= beside
        alert = alert or listed_alert
        off_form = None if sent.listed.on_form else sent.listed
    if problems:
        # What was typed is as of when the form was shown, of the places it
        # showed; a recount asks again for its own items only.
        return as_sent(
            problems=problems, alert=alert, tree=tree, left=left(), off_form=off_form
        )
    # Filtered to some items, a place of a store's form with nothing typed
    # was not counted, the store's own too.
    filtered = shown.only is not None or bool(shown.search)
    counting = [
        place_id
        for place_id, each in sent.places.items()
        if not (areas and filtered) or each.anything
    ]
    moves = since.shown()
    fixes = fixed(
        moves,
        {place_id: each.arrived for place_id, each in sent.places.items()},
    )
    aside = since.aside()
    fix_lines = fixed_lines(fixes, since.held())
    if not counting and not fixes:
        # Nothing typed at any place: nothing to count, so the form again,
        # as sent, saying so (inventory-tng-fxsy).
        log.info("count refused: nothing typed", location_id=place.id)
        return as_sent(alert=NOTHING_TYPED, left=left(), status_code=400)

    # Bound again, as checked, for put_right.
    sent_key, across = key, place_ids
    # The place that refused it (counts.NotCountable), as it was then.
    retired: int | None = None
    # What it showed otherwise, as it is now (units_shown.ShownOtherwise).
    shown_now: Now | None = None

    def put_right() -> StoreCounted:
        nonlocal retired, shown_now
        try:
            return record_store_count(
                session,
                sent_key,
                signed.admin.id,
                across,
                at,
                {place_id: sent.places[place_id] for place_id in counting},
                now(),
                aside=aside,
                fixed=fix_lines,
                shown=shown_as,
            )
        except NotCountable as broken:
            retired = broken.location_id
            raise
        except ShownOtherwise as broken:
            shown_now = broken.now
            raise

    result, broken = refusal(
        session,
        put_right,
        WHOLE,
        COUNTABLE,
        UNTRACKED,
        ONCE,
        CLOSED_MEANWHILE,
        CHANGED,
        SENT_AGAIN,
        SHOWN_OTHERWISE,
    )
    if broken is not None:
        log.info("count refused by a rule", location_id=place.id, rule=broken)
        if broken == SENT_AGAIN:
            # The saved count is listed on Counts, to see what went through.
            return redirect(COUNTS, notice="count_saved_already", section="count")
        if shown_now is not None:
            # Changed between the check above and the save: the form as
            # sent, in the units and sizes now, each box of one changed
            # saying so.
            words, said = asked_again(
                session,
                form,
                place.id,
                place_ids,
                shown_now,
                separator=decimal_separator(request),
            )
            return as_sent(problems=words, alert=said, left=left())
        # Refused for another reason, shown again keeping what was typed, in
        # the units and sizes now: one changed since is said too. The
        # savepoint's rollback keeps the rows read before it as they were
        # then, so they are read again, as the words are.
        session.expire_all()
        if broken == COUNTABLE and (retired is None or retired == place.id):
            # As for a place there is not, as the form's own page answers.
            return not_found()
        changed, said = kept_as_now(
            session,
            form,
            key,
            sent.places.values(),
            place.id,
            place_ids,
            separator=decimal_separator(request),
        )
        if broken == COUNTABLE and retired is not None:
            # Areas retired, or emptied, since: the form as it is now,
            # without them, keeping what was typed, to check and send
            # again, saying what left them since it was first shown; a
            # recount asks no more there, but where it went (_asked_after)
            # (Ali, 2026-11-22, inventory-tng-ygk5).
            fresh, gone = _gone_now(
                session, place.id, place_ids, retired, gone, at.as_of
            )
            log.info(
                "count refused: areas gone from the form",
                location_id=place.id,
                gone_ids=sorted(fresh),
            )
            tree = queries.place_tree(session, place)
            return again(
                None,
                values=typed,
                problems=changed,
                gone_now=fresh,
                items=_asked_after(session, shown, asked, fresh, gone, tree),
                recount={
                    place_id: each
                    for place_id, each in figures.items()
                    if place_id not in fresh
                },
                gone=gone,
                tree=tree,
                caution=said,
                status_code=409,
            )
        if broken == CHANGED:
            # What was typed may be of before that save or after it, of
            # items it cannot tell: the form as it is now, with a new key,
            # keeping what was typed, to check and send again.
            return asking(
                None,
                values=typed,
                problems=changed,
                alert=SAVED_AS_SHOWN,
                caution=said,
                status_code=409,
            )
        # Checked again, the units as they are now mark the lines it broke;
        # a rule that marks none is said above the form, and a unit or size
        # changed since beside its boxes and below that.
        marked: dict[str, Words] = {}
        for place_id, each in sent.places.items():
            _, wrong = item_counts(
                session, each.found, separator=decimal_separator(request)
            )
            marked |= fields_at(place.id, place_id, wrong)
        return as_sent(
            problems=marked | changed,
            alert=said if marked else RULES[broken],
            caution=None if marked else said,
            left=left(),
            status_code=400,
        )
    assert result is not None
    session.commit()
    _saved(place.id, result, fixes)
    counted = result.places
    moved = {place_id: each.moved for place_id, each in counted.items() if each.moved}
    if not moved:
        notice = (
            "counted"
            if fixes or any(each.differed for each in counted.values())
            else "counted_same"
        )
        if not areas:
            return redirect(COUNTS, notice=notice, section="count")
        # Back to the form, the places picked for something else here and
        # an open package nobody recorded picked again (count_forms.PICKED).
        params = shown.params() | {
            picker: form[picker] for picker in PICKED if form.get(picker)
        }
        return redirect(f"{COUNTS}/{place.id}", notice=notice, **params)
    amounts = {place_id: sent.places[place_id].typed() for place_id in counted}
    saved = sum(len(typed) for typed in amounts.values()) - sum(
        len(each) for each in moved.values()
    )
    return again(
        None,
        recount=_moved_figures(moved, amounts, arrived_at(aside, fix_lines)),
        alert=moved_meanwhile(saved),
        # Saved: the moves from the areas gone are in the records now.
        gone=NONE_GONE,
        status_code=409,
    )


def _saved(place_id: int, result: StoreCounted, fixes: Sequence[Fix]) -> None:
    """Measure and log a count form of the place saved, and the moves it
    fixed."""
    counted = result.places
    if any(each.written for each in counted.values()):
        places_counted.record(len(counted))
        log.info("count saved", location_id=place_id, places=len(counted))
    if result.fixed:
        # A move fixed in two forms is one move, unless one form came out
        # more and the other less.
        for to_id, from_id, item_id, found in sorted(
            {(fix.to_id, fix.from_id, fix.item_id, fix.difference > 0) for fix in fixes}
        ):
            moves_fixed.add(1, {"direction": "found" if found else "lost"})
            log.info(
                "count move fixed",
                location_id=to_id,
                from_location_id=from_id,
                item_id=item_id,
            )


def _moved_figures(
    moved: Mapping[int, Mapping[int, tuple[Decimal, Decimal]]],
    typed: Mapping[int, Mapping[int, Decimal]],
    arrived: Mapping[tuple[int, int], Decimal],
) -> dict[int, dict[int, Figures]]:
    """A recount's figures of what moved while it was counted, by place
    and item: what was typed, what came in and what went out. The moves
    from the areas gone, and their fixes, are in the records now, while
    what was typed counted what each place held before them: what
    `arrived` is added to it, so the recount says all it holds."""
    return {
        place_id: {
            item_id: (
                typed[place_id][item_id] + arrived.get((place_id, item_id), Decimal(0)),
                came,
                went,
            )
            for item_id, (came, went) in each.items()
        }
        for place_id, each in moved.items()
    }


def _gone_now(
    session: Session,
    root_id: int,
    place_ids: Collection[int],
    retired: int,
    gone: Gone,
    as_of: int,
) -> tuple[set[int], Gone]:
    """The areas of `place_ids` that may no longer be counted, of a count
    form of `root_id` sent as of moment `as_of` that `retired` refused; and the areas
    gone from it, with them. The one refused, if brought back since the
    refusal, is said gone all the same, as the save was refused for it."""
    fresh = queries.not_countable(session, place_ids) - {root_id} or {retired}
    return fresh, Gone(
        tuple(sorted({*gone.ids, *fresh})),
        gone.since if gone.ids else as_of,
    )


def _asked_after(
    session: Session,
    shown: Shown,
    asked: Mapping[int, Collection[int]],
    fresh: Collection[int],
    gone: Gone,
    tree: PlaceTree,
) -> dict[int, set[int]]:
    """What a recount refused as areas on it are gone (`fresh`) asks for
    again, by place: the same, but at those; and, of the items shown, what
    left the areas gone for a place in the store it does not ask at, there
    too, so it is shown moved there, not taken out of the store (Ali,
    2026-11-22, inventory-tng-ygk5), of its place's `tree`
    (queries.place_tree). Nothing, for a form asking for nothing else,
    which is shown again whole. What left them is read here, as the
    places it adds decide the moment of the form shown again, which it
    reads them as of."""
    items = {
        place_id: set(each)
        for place_id, each in asked.items()
        if place_id not in fresh and each
    }
    if items:
        ids = shown.ids(session)
        in_tree = {each.id for each, _, _ in tree}
        for move in moved_out(session, gone.ids, gone.since):
            if (
                move.to_id in in_tree
                and move.to_id not in gone.ids
                and (ids is None or move.item_id in ids)
            ):
                items.setdefault(move.to_id, set()).add(move.item_id)
    return items


@router.post(f"{COUNTS}/{{location_id:int}}/bring-back")
def bring_back(
    request: Request,
    signed: AdminSignedIn,
    session: DbSession,
    location_id: int,
    form: TextForm,
    shown: ShownItems,
) -> Response:
    """The count form's Bring it back: an area gone from it, retired and
    emptied while it was counted, brought back as its own page does
    (places/locations.restore), and the same form shown again with it,
    keeping what was typed (Ali, 2026-11-22, inventory-tng-ygk5), in the
    units and sizes now, saying so beside a box of one changed since."""
    carried = _sent(session, form, location_id)
    area_id = row_id(form.get("area_id", ""))
    if carried is None or area_id not in carried.gone.ids:
        return not_found()
    place, key, at, place_ids, gone = carried
    tree = queries.place_tree(session, place)
    area, label = next(
        (
            (each, queries.place_label(each, path))
            for each, path, _ in tree
            if each.id == area_id
        ),
        (None, ""),
    )
    if area_id == place.id or area is None:
        # Not one of the place's areas, as only a hand-made form names: none
        # this form may bring back.
        return not_found()
    # What left the areas gone, and what of it each place holds, read when
    # first needed, once, as count reads them.
    since = _since_shown(session, carried)
    left = since.left
    sent = read_count(
        session,
        form,
        place.id,
        place_ids,
        since.asks_for,
        separator=decimal_separator(request),
    ).places
    words, said = kept_as_now(
        session,
        form,
        key,
        sent.values(),
        place.id,
        place_ids,
        separator=decimal_separator(request),
    )
    again = partial(
        _count_page,
        request,
        session,
        signed,
        place,
        shown=shown,
        values=form,
        problems=words,
        items={place_id: each.items for place_id, each in sent.items()},
        recount={place_id: each.recount for place_id, each in sent.items()},
        gone=gone,
    )

    def restore() -> bool:
        try:
            locations.restore(session, area)
        except RowGone:  # Current already, as after a second press.
            return False
        return True

    brought, broken = attempt(session, restore)
    if broken is not None:
        log.info(
            "count area not brought back",
            location_id=place.id,
            area_id=area.id,
            rule=broken,
        )
        # Nothing changed: the tree and the areas as read already.
        return again(
            at,
            places=place_ids,
            alert=RULES[broken],
            tree=tree,
            left=left(),
            status_code=409,
        )
    session.commit()
    if brought:
        areas_brought_back.add(1)
        log.info("count area brought back", location_id=place.id, area_id=area.id)
    # Shown with it, as of the same moment: what it saw there counted too,
    # and what left the areas gone as read already, split at that moment.
    # The tree is read again, as bringing the area back changed it.
    listed = [*place_ids, area.id]
    # Brought back, so done: words beside a box refuse nothing here, as
    # nothing is saved, but ask for a look before the next Save (Ali,
    # 2026-11-23, inventory-tng-itfm).
    return again(
        Moment(at.as_of, seen_up_to(session, listed, at.as_of)),
        places=listed,
        left=left(),
        alert=said,
        caution=brought_back(label),
        status_code=200,
    )
