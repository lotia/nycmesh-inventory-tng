"""Counts and adjustments: the pages an administrator puts the records right
with (docs/decisions/0003). The writing is stock/store_counts.py's
(record_store_count) and stock/counts.py's (record_adjustment); these
pages check what was typed and say what is wrong.

Each form carries a key made when it was shown, so sending it twice writes
it once. Sent again finding the open package nobody recorded otherwise
(another place or package, or one where the first send found none), it
writes nothing more and goes back to Counts saying it was saved already
(Ali, 2026-11-22, inventory-tng-53im). A count also carries the place's
latest ledger entry as it was shown, and how many there it saw
(moments.Moment): the count is true for that moment, so a save made at the
place meanwhile still applies, and an item it moved comes back to count
again. The moment is read without the place's lock, so showing the form
holds up no save there; a save under way as it was shown, committed after
with an id inside the moment, refuses the count (changed_since_shown). Which
items that save moved cannot be told, so the form is shown again as it is
now, keeping what was typed, to be checked and sent again.

The pages check what was typed before writing, but an item's unit may
change to each between that check and the write: the ledger then refuses
the line (movement_whole_units), and the form is shown again with that
line marked, and nothing written. So too a place retired meanwhile, which
the ledger refuses (location_countable) unless the form was written there
before.

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
the places it shows (forms.PLACES); its moment is read across them all,
and the send holds them all before writing any (stock/store_counts.py).
An area retired and empty is left out. Filtered to some items, an area
with nothing typed is not counted; with nothing typed at any place,
nothing is, and the form is shown again saying so (Ali, 2026-11-21,
inventory-tng-fxsy).

An area on the form that is retired, or a retired one emptied, while it
is counted may no longer be counted: the save is refused whole, and the
form shown again without it, keeping what was typed (Ali, 2026-11-22,
inventory-tng-ygk5). The form then carries the areas gone from it and
the moment it was first shown (forms.GONE), so it shows, until saved,
what left them since (stock/count_gone_areas.py): what went to a place on
it is set aside from what that place's own lines count, and each move is
taken as right unless its amount is fixed, in each form it arrived in,
the difference then lost or found there (stock/count_arrivals.py). One emptied can be brought back
from the form (bring_back), as its own page does
(places/locations.restore), and the form shown again with it.
"""

import uuid
from collections.abc import Collection, Mapping, Sequence
from decimal import Decimal
from functools import partial
from typing import Annotated, Any, NamedTuple
from urllib.parse import urlencode

import structlog
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response
from opentelemetry import metrics
from sqlalchemy import ColumnElement, case, func
from sqlalchemy.orm import Session

from inventory_tng.admins.guards import AdminSignedIn, SignedIn
from inventory_tng.catalogue.items_listing import SEARCHED
from inventory_tng.core.db_errors import Gone as RowGone
from inventory_tng.core.db_errors import attempt, refusal
from inventory_tng.core.db_sessions import DbSession, SnapshotSession, by_name
from inventory_tng.core.phrases import Words, gettext, in_query, lazy_gettext, ngettext
from inventory_tng.core.sent_forms import form_key
from inventory_tng.models.admins import Admin
from inventory_tng.models.base import INTEGER_MAX, whole
from inventory_tng.models.catalogue import Item, ItemPackage
from inventory_tng.models.places import Location
from inventory_tng.models.stock import AdjustmentReason, LedgerTransaction, OpenPackage
from inventory_tng.places import locations
from inventory_tng.site_settings.paths import SETTINGS
from inventory_tng.stock import needs_action, over_recorded, queries
from inventory_tng.stock.balance import countable, takeable
from inventory_tng.stock.count_arrivals import (
    Aside,
    arrived_at,
    fixed,
    fixed_lines,
    gone_moves,
    set_aside,
)
from inventory_tng.stock.count_gone_areas import (
    Leaving,
    areas_leaving,
    gone_words,
    moved_sections,
)
from inventory_tng.stock.count_groups import (
    FormRow,
    Group,
    form_order,
    form_row,
    label,
)
from inventory_tng.stock.counts import NotCountable, record_adjustment
from inventory_tng.stock.forms import (
    CLOSED_MEANWHILE,
    COUNTED,
    EXTRA_ITEM,
    EXTRA_PLACE,
    FOUND_PLACE,
    LOOSE_FIELD,
    NONE_GONE,
    PICKED,
    TYPE_IT_HERE,
    Gone,
    Listed,
    adjustment,
    field_at,
    fields_at,
    gone_listed,
    item_counts,
    places_listed,
    read_count,
)
from inventory_tng.stock.ledger import LOOSE, Form, tracks_opened
from inventory_tng.stock.moments import (
    Moment,
    expected_at,
    expected_forms_at,
    moment,
    seen_up_to,
)
from inventory_tng.stock.needs_action import OnlyItem
from inventory_tng.stock.paths import ADJUSTMENTS, COUNTS
from inventory_tng.stock.store_counts import StoreCounted, record_store_count
from inventory_tng.web.admin_pages import admin_page, current_rows, now, text_form
from inventory_tng.web.forms import CHANGED, RULES, row_id
from inventory_tng.web.listing import UNASKED, AskedView, Sort, Sorting, View, order
from inventory_tng.web.navigation import SECTION_COUNTS
from inventory_tng.web.pages import add_notices, not_found, redirect

log = structlog.get_logger(__name__)
meter = metrics.get_meter(__name__)

router = APIRouter(include_in_schema=False)

# The ledger rules a race can break past the page's checks (module
# docstring).
WHOLE = "movement_whole_units"
COUNTABLE = "location_countable"
# An open package found that its item does not follow once opened, and a
# form counted twice: said as rules are.
UNTRACKED = "open_package_untracked"
ONCE = "movement_item_once"
# A form saved, sent again finding an open package nobody recorded
# otherwise (store_counts._count_at): nothing more is written, and Counts
# says so (Ali, 2026-11-22, inventory-tng-53im).
SENT_AGAIN = "open_package_key"
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
COUNTS_TITLE = lazy_gettext("Counts and adjustments")
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
# arrived than was moved, each form it arrived in on its own
# (inventory-tng-ygk5).
moves_fixed = meter.create_counter(
    "inventory.count.moves_fixed",
    unit="{move}",
    description="Moves from retired areas emptied while counted whose amount "
    "a count form fixed, each form they arrived in apart, by whether more "
    "arrived (found) or less (lost)",
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
    picked for it lists (forms.Listed): what was typed is kept."""
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
ADJUSTED = lazy_gettext("Recorded.")
add_notices(
    {
        "counted": COUNTED_NOTICE,
        "counted_same": COUNTED_SAME,
        "count_saved_already": SAVED_ALREADY,
        "adjusted": ADJUSTED,
    }
)

TextForm = Annotated[dict[str, str], Depends(text_form)]


def _items(session: Session) -> list[Item]:
    """What can be counted or adjusted: current items, and retired ones with
    stock left, which may still be written off."""
    return by_name(session, Item, takeable())


def _places(session: Session) -> list[Location]:
    """Where can be counted or adjusted (balance.countable)."""
    return by_name(session, Location, countable())


# Translators: a count, as a kind of save, before what it saved.
COUNT_KIND = lazy_gettext("Count")
# How it sorts: newest first, as it always has, or by a heading; What by
# its words, Count or the reason (Ali, 2026-11-15, inventory-tng-qwb), in
# the language spoken.
SORTING = Sorting(
    "when",
    {
        "when": Sort.of(LedgerTransaction.id, first="desc"),
        "what": Sort.of(
            case(
                (LedgerTransaction.kind == "count", func.lower(in_query(COUNT_KIND))),
                else_=func.lower(AdjustmentReason.name),
            )
        ),
        "where": Sort.of(func.lower(Location.name)),
        "by": Sort.of(func.lower(Admin.username)),
    },
)


def _view(
    asked: AskedView,
    place: str = "",
    kind: str = "",
    admin: str = "",
) -> View:
    """The list's view as its query string asks: a place or an admin not a
    number, or a kind not offered, is no filter (Ali, 2026-11-15)."""
    place_id = whole(place, 1, INTEGER_MAX)
    admin_id = whole(admin, 1, INTEGER_MAX)
    filters = {
        "place": str(place_id or ""),
        "kind": kind if kind in queries.KINDS else "",
        "admin": str(admin_id or ""),
    }
    return View.read(COUNTS, SORTING, asked, filters=filters)


def _recent(session: Session, view: View) -> tuple[list[dict[str, Any]], View]:
    """The counts and adjustments the view picks, in its order, one page
    of them, with their lines; and the view counted."""
    where: list[ColumnElement[bool]] = []
    if place := view.filtered("place"):
        where.append(queries.count_place == int(place))
    if kind := view.filtered("kind"):
        where.append(LedgerTransaction.kind == kind)
    if admin := view.filtered("admin"):
        where.append(LedgerTransaction.admin_id == int(admin))
    view = view.counted(queries.counts_and_adjustments_found(session, *where))
    rows = queries.counts_and_adjustments(
        session,
        *where,
        order=order(view, LedgerTransaction.id),
        limit=view.per_page,
        offset=view.offset,
    )
    log.debug("counts listed", found=view.found, sort=view.by, page=view.page)
    lines: dict[int, list[tuple[Item, Decimal]]] = {}
    for transaction_id, item, amount in queries.count_lines(
        session, [row[0].id for row in rows]
    ):
        lines.setdefault(transaction_id, []).append((item, amount))
    return [
        {
            "transaction": transaction,
            "admin": admin,
            "place": place,
            "found": transaction.to_location_id is not None,
            "reason": reason or "",
            "lines": lines.get(transaction.id, []),
        }
        for transaction, admin, place, reason in rows
    ], view


def _counts_page(
    request: Request,
    session: Session,
    signed: SignedIn,
    *,
    view: View | None = None,
    problems: Mapping[str, Words] | None = None,
    values: Mapping[str, str] | None = None,
) -> HTMLResponse:
    recent, view = _recent(session, view or _view(UNASKED))
    # The filters offer where and by whom there are any.
    return admin_page(
        request,
        signed,
        "stock/counts.html",
        {
            "title": COUNTS_TITLE,
            "here": COUNTS,
            "settings_path": SETTINGS,
            "places": _places(session),
            "items": _items(session),
            "reasons": current_rows(session, AdjustmentReason),
            "recent": recent,
            "view": view,
            "kinds": queries.KINDS,
            "count_kind": COUNT_KIND,
            "counted_places": queries.places_counted(session),
            "counters": queries.counters(session),
            "key": uuid.uuid4(),
            "problems": problems,
            "values": values,
        },
    )


@router.get(COUNTS)
def counts(
    request: Request,
    signed: AdminSignedIn,
    session: DbSession,
    view: Annotated[View, Depends(_view)],
) -> Response:
    return _counts_page(request, session, signed, view=view)


@router.get(f"{COUNTS}/place")
def choose_place(location_id: str = "") -> Response:
    """The "Count a place" form, sent as a GET, goes to that place's count."""
    if not location_id.isdigit():
        return redirect(COUNTS)
    return redirect(f"{COUNTS}/{location_id}")


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
    session: Session, root: Location, places: Collection[int] | None
) -> list[tuple[Location, tuple[str, ...]]]:
    """The places a count form shows, each with its path: the place and
    every area in it that may be counted, as they are now; or of those, the
    places `places` names, in that order, then any not in it now (an area
    moved out of it since), under its own name."""
    tree = queries.countable_tree(session, root)
    if places is None:
        return tree
    shown = [(place, path) for place, path in tree if place.id in places]
    gone = set(places) - {place.id for place, _ in shown}
    for place in by_name(session, Location, Location.id.in_(gone)):
        shown.append((place, (place.name,)))
    return shown


def _listed_words(
    session: Session, root: Location, listed: Listed
) -> tuple[Words, dict[str, Words]]:
    """What a count form says of something else here naming an item the
    place picked for it lists (forms.Listed): above the form, and beside
    the item picked."""
    item = session.get_one(Item, listed.item_id)
    ((place, path),) = _shown_places(session, root, [listed.place_id])
    where = label(place, path)
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
    recount: Mapping[int, Mapping[int, tuple[Decimal, Decimal, Decimal]]] | None = None,
    alert: Words | None = None,
    more_alerts: Sequence[Words] = (),
    picked: Mapping[str, str] | None = None,
    gone: Gone = NONE_GONE,
    leaving: Leaving | None = None,
    caution: Words | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    """The count form of a place and the areas in it, with the records as
    of `at`, or now if None, read across the places it shows. Of everything
    there, or a recount of `items` only, by place, which the form carries.
    With `recount`, by place, a recount of its items: what was typed for
    each and what came in and went out while it was counted. Of those, only
    the items `shown`, but a recount's every one. `places`, the places the
    form showed when sent; else every one that may be counted now.
    `picked`, the places picked for something else here and an open
    package nobody recorded at the last save (forms.PICKED). `gone`, the
    areas gone from it, retired, and what left them since it was first
    shown, up to its moment, shown under the places it went to
    (count_gone_areas), whose own lines count what they held before
    (count_arrivals.set_aside); `leaving`, those areas as read already, if
    they were; `more_alerts`, under `alert`; `caution`, words above the
    form, said as a warning is."""
    asked = {
        place_id: set(each)
        for place_id, each in [*(items or {}).items(), *(recount or {}).items()]
        if each
    }
    ids = shown.ids(session)
    if leaving is None and gone.ids:
        leaving = areas_leaving(session, place, gone)
    groups_shown = _shown_places(
        session, place, places if places is not None else asked or None
    )
    ids_shown = [each.id for each, _ in groups_shown]
    at = at or moment(session, *ids_shown)
    as_of = at.as_of
    every = set().union(*asked.values()) if asked else None
    was_at = expected_at(session, ids_shown, as_of, every)
    # Of what left the areas gone, only the items shown; each place's own
    # lines count what it held before.
    moves = [
        move
        for move in gone_moves(session, gone, as_of, ids_shown)
        if ids is None or move.item_id in ids
    ]
    aside = set_aside(moves)
    for place_id, here in aside.items():
        held = was_at.get(place_id, {})
        for (item_id, _), amount in here.items():
            if item_id in held:
                held[item_id] -= amount
                if not held[item_id]:
                    del held[item_id]
    listed = _items(session)
    named = {item.id: item for item in listed}
    openings = over_recorded.openings_waiting_at(session, ids_shown, as_of)
    if ids is not None:
        listed = [item for item in listed if item.id in ids]
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
    only = shown.only
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
    forms = _forms(
        session,
        as_of,
        {each: [item.id for item, _ in found] for each, found in rows.items()},
        openings,
        aside,
    )
    groups = [
        Group(
            each,
            path,
            field_at(place.id, each.id, ""),
            rows[each.id],
            forms.get(each.id, {}),
            sorted(asked.get(each.id, ())),
            (recount or {}).get(each.id),
        )
        for each, path in groups_shown
    ]
    moved = None
    if leaving is not None:
        moved = moved_sections(session, place, groups, leaving, moves, named)
    # Something else here and an open package nobody recorded may be found
    # in any place shown but a retired area (forms.PICKED).
    choices = [
        group
        for group in groups
        if group.place.retired_at is None or group.place.id == place.id
    ]
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
            "found_packages": [
                found
                for found in _followed(session)
                if ids is None or found[0].item_id in ids
            ],
            "recount": recount,
            # What a place it may be found in does not list already, and the
            # item picked, to keep it picked on a form shown again: a
            # recount adds its line (forms.read_count).
            "others": [
                item
                for item in listed
                if not all(item.id in was[group.place.id] for group in choices)
                or str(item.id) == (values or {}).get(EXTRA_ITEM)
            ],
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
        400 if problems else status_code,
    )


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
    # picked at the last save come back picked (forms.PICKED).
    place = queries.countable_place(session, location_id)
    if place is None:
        return not_found()
    picked = {EXTRA_PLACE: extra_location_id, FOUND_PLACE: found_location_id}
    return _count_page(
        request, session, signed, place, None, shown=shown, picked=picked
    )


def _forms(
    session: Session,
    as_of: int,
    item_ids: Mapping[int, Sequence[int]],
    openings: Mapping[int, Mapping[int, Collection[int]]],
    aside: Mapping[int, Aside] | None = None,
) -> dict[int, dict[int, list[FormRow]]]:
    """At each place, by id, the items of `item_ids` there whose stock was
    in more than loose units, as of the form, each form a row: sealed
    packages, by how many; open packages; loose. Others are counted by
    their total, as before, but an item of an opening waiting there,
    recorded more than opened (`openings`, by place,
    over_recorded.openings_waiting_at): it is counted by form, with a row of
    each package it opened sealed, though the records hold none, as a total
    puts neither of its ends right (docs/decisions/0020). Less, by place,
    what is `aside` there (count_arrivals.set_aside). The same number of
    queries however many places."""
    every = sorted(set().union(*item_ids.values())) if item_ids else []
    found = expected_forms_at(session, item_ids.keys(), as_of, every)
    packages = {
        package.id: package for package in queries.packages_of_items(session, every)
    }
    opened = {row.id: row for row in queries.open_at(session, item_ids.keys(), every)}
    return {
        place_id: _place_forms(
            set(items),
            _less(found.get(place_id, {}), (aside or {}).get(place_id, {})),
            packages,
            {
                open_id: row
                for open_id, row in opened.items()
                if row.location_id == place_id and row.item_id in items
            },
            openings.get(place_id, {}),
        )
        for place_id, items in item_ids.items()
    }


def _less(
    found: Mapping[tuple[int, Form], Decimal], aside: Mapping[tuple[int, Form], Decimal]
) -> dict[tuple[int, Form], Decimal]:
    """What was found of each form, less what is set aside of it: none
    where that leaves nothing."""
    less = {form: n - aside.get(form, Decimal(0)) for form, n in found.items()}
    return {form: n for form, n in less.items() if n}


def _place_forms(
    item_ids: Collection[int],
    found: Mapping[tuple[int, Form], Decimal],
    packages: Mapping[int, ItemPackage],
    opened: Mapping[int, OpenPackage],
    openings: Mapping[int, Collection[int]],
) -> dict[int, list[FormRow]]:
    """_forms at one place, from what it read."""
    held: dict[int, dict[Form, Decimal]] = {}
    for (item_id, form), amount in found.items():
        # Of its items only; and one closed since is not counted: its row
        # is gone from the place.
        if item_id in item_ids and (
            form.open_package_id is None or form.open_package_id in opened
        ):
            held.setdefault(item_id, {})[form] = amount
    for row in opened.values():
        held.setdefault(row.item_id, {}).setdefault(
            Form(open_package_id=row.id), Decimal(0)
        )
    for item_id in item_ids:
        for package_id in openings.get(item_id, ()):
            held.setdefault(item_id, {}).setdefault(
                Form(package_id=package_id), Decimal(0)
            )

    rows: dict[int, list[FormRow]] = {}
    for item_id, forms in held.items():
        if set(forms) <= {LOOSE}:
            continue
        each = rows.setdefault(item_id, [])
        for form in sorted(forms, key=lambda form: form_order(form, packages)):
            each.append(form_row(item_id, form, forms[form], packages, opened))
        if not any(row.field.startswith(LOOSE_FIELD) for row in each):
            each.append(form_row(item_id, LOOSE, Decimal(0), packages, opened))
    return rows


def _line_of(session: Session, as_of: int, listed: Listed) -> str:
    """The field a count form, as of moment `as_of`, shows an item's line at
    a place by: its total, or, counted by form (_forms), its loose units."""
    place_id, item_id = listed.place_id, listed.item_id
    openings = over_recorded.openings_waiting_at(session, [place_id], as_of)
    by_form = _forms(session, as_of, {place_id: [item_id]}, openings)[place_id]
    return f"{LOOSE_FIELD if item_id in by_form else COUNTED}{item_id}"


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
    the places it showed, and the areas gone from it (forms.Gone)."""

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
    sent = read_count(session, form, place.id, place_ids, at.as_of)
    asked = {place_id: each.items for place_id, each in sent.places.items()}
    # The form shown again: as sent, or with `at` None, as it is now.
    again = partial(
        _count_page, request, session, signed, place, shown=shown, gone=gone
    )
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
    if sent.listed is not None:
        log.info(
            "count refused: something else here listed there",
            location_id=place.id,
            found_location_id=sent.listed.place_id,
            item_id=sent.listed.item_id,
        )
        listed_alert, beside = _listed_words(session, place, sent.listed)
        problems |= beside
        if not sent.listed.on_form:
            # A recount's line added for it (forms.read_count), marked.
            line = _line_of(session, at.as_of, sent.listed)
            problems[field_at(place.id, sent.listed.place_id, line)] = TYPE_IT_HERE
        alert = alert or listed_alert
    if problems:
        # What was typed is as of when the form was shown, of the places it
        # showed; a recount asks again for its own items only.
        return again(
            at,
            places=place_ids,
            problems=problems,
            values=form,
            alert=alert,
            items=asked,
        )
    # Filtered to some items, a place of a store's form with nothing typed
    # was not counted, the store's own too.
    filtered = shown.only is not None or bool(shown.search)
    counting = [
        place_id
        for place_id, each in sent.places.items()
        if not (areas and filtered) or each.anything
    ]
    # What came from the areas gone from it, as the form showed it: set
    # aside from what each place's lines count, and the moves fixed
    # (stock/count_arrivals.py).
    moves = gone_moves(session, gone, at.as_of, place_ids)
    fixes = fixed(
        moves,
        {place_id: each.arrived for place_id, each in sent.places.items()},
    )
    if not counting and not fixes:
        # Nothing typed at any place: nothing to count, so the form again,
        # as sent, saying so (inventory-tng-fxsy).
        log.info("count refused: nothing typed", location_id=place.id)
        return again(
            at,
            places=place_ids,
            values=form,
            alert=NOTHING_TYPED,
            items=asked,
            status_code=400,
        )

    # Bound again, as checked, for put_right.
    sent_key, across = key, place_ids
    # The place that refused it (counts.NotCountable), as it was then.
    retired: int | None = None

    def put_right() -> StoreCounted:
        nonlocal retired
        try:
            return record_store_count(
                session,
                sent_key,
                signed.admin.id,
                across,
                at,
                {place_id: sent.places[place_id] for place_id in counting},
                now(),
                aside=set_aside(moves),
                fixed=fixed_lines(fixes),
            )
        except NotCountable as broken:
            retired = broken.location_id
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
    )
    if broken is not None:
        log.info("count refused by a rule", location_id=place.id, rule=broken)
        if broken == SENT_AGAIN:
            # The saved count is listed on Counts, to see what went through.
            return redirect(COUNTS, notice="count_saved_already", section="count")
        if broken == COUNTABLE:
            if retired is None or retired == place.id:
                # As for a place there is not, as the form's own page answers.
                return not_found()
            # Areas retired, or emptied, since: the form as it is now,
            # without them, keeping what was typed, to check and send
            # again, saying what left them since it was first shown; a
            # recount asks no more there (Ali, 2026-11-22, inventory-tng-ygk5).
            # The one refused, if brought back since the refusal: said gone
            # all the same, as the save was refused for it.
            fresh = queries.not_countable(session, place_ids) - {place.id} or {retired}
            gone = Gone(
                tuple(sorted({*gone.ids, *fresh})),
                gone.since if gone.ids else at.as_of,
            )
            log.info(
                "count refused: areas gone from the form",
                location_id=place.id,
                gone_ids=sorted(fresh),
            )
            leaving = areas_leaving(session, place, gone)
            first, *more = gone_words(leaving, fresh)
            return again(
                None,
                values=typed,
                alert=first,
                more_alerts=more,
                items={
                    place_id: each
                    for place_id, each in asked.items()
                    if place_id not in fresh
                },
                gone=gone,
                leaving=leaving,
                status_code=409,
            )
        if broken == CHANGED:
            # What was typed may be of before that save or after it, of
            # items it cannot tell: the form as it is now, with a new key,
            # keeping what was typed, to check and send again.
            return again(
                None,
                values=typed,
                alert=SAVED_AS_SHOWN,
                items=asked,
                status_code=409,
            )
        # Checked again, the units as they are now mark the lines it broke.
        problems = {}
        for place_id, each in sent.places.items():
            _, wrong = item_counts(session, each.found)
            problems |= fields_at(place.id, place_id, wrong)
        return again(
            at,
            places=place_ids,
            problems=problems,
            values=form,
            alert=None if problems else RULES[broken],
            items=asked,
            status_code=400,
        )
    assert result is not None
    session.commit()
    counted = result.places
    if any(each.written for each in counted.values()):
        places_counted.record(len(counted))
        log.info("count saved", location_id=place.id, places=len(counted))
    if result.fixed:
        for fix in fixes:
            moves_fixed.add(1, {"direction": "found" if fix.difference > 0 else "lost"})
            log.info(
                "count move fixed",
                location_id=fix.to_id,
                from_location_id=fix.from_id,
                item_id=fix.item_id,
            )
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
        # an open package nobody recorded picked again (forms.PICKED).
        params = shown.params() | {
            picker: form[picker] for picker in PICKED if form.get(picker)
        }
        return redirect(f"{COUNTS}/{place.id}", notice=notice, **params)
    amounts = {place_id: sent.places[place_id].typed() for place_id in counted}
    # The moves from the areas gone, and their fixes, are in the records
    # now, while what was typed counted what each place held before them:
    # what arrived is added to it, so the recount says all it holds.
    arrived = arrived_at(moves, fixes)
    saved = sum(len(typed) for typed in amounts.values()) - sum(
        len(each) for each in moved.values()
    )
    return again(
        None,
        recount={
            place_id: {
                item_id: (
                    amounts[place_id][item_id]
                    + arrived.get((place_id, item_id), Decimal(0)),
                    came,
                    went,
                )
                for item_id, (came, went) in each.items()
            }
            for place_id, each in moved.items()
        },
        alert=moved_meanwhile(saved),
        # Saved: the moves from the areas gone are in the records now.
        gone=NONE_GONE,
        status_code=409,
    )


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
    keeping what was typed (Ali, 2026-11-22, inventory-tng-ygk5)."""
    carried = _sent(session, form, location_id)
    area_id = row_id(form.get("area_id", ""))
    if carried is None or area_id not in carried.gone.ids:
        return not_found()
    place, _, at, place_ids, gone = carried
    leaving = areas_leaving(session, place, gone)
    if area_id == place.id or area_id not in leaving.order:
        # Not one of the place's areas, as only a hand-made form names: none
        # this form may bring back.
        return not_found()
    asked = {
        place_id: each.items
        for place_id, each in read_count(
            session, form, place.id, place_ids, at.as_of
        ).places.items()
    }
    again = partial(
        _count_page,
        request,
        session,
        signed,
        place,
        shown=shown,
        values=form,
        items=asked,
        gone=gone,
    )
    area = session.get_one(Location, area_id)

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
        return again(at, places=place_ids, alert=RULES[broken], status_code=409)
    session.commit()
    if brought:
        areas_brought_back.add(1)
        log.info("count area brought back", location_id=place.id, area_id=area.id)
    # Shown with it, as of the same moment: what it saw there counted too.
    listed = [*place_ids, area.id]
    return again(
        Moment(at.as_of, seen_up_to(session, listed, at.as_of)),
        places=listed,
        caution=brought_back(leaving.labels[area.id]),
    )


@router.post(ADJUSTMENTS)
def adjust(
    request: Request, signed: AdminSignedIn, session: DbSession, form: TextForm
) -> Response:
    key = form_key(form.get("key", ""))
    asked, problems = adjustment(session, form, key)
    if asked is None:
        return _counts_page(request, session, signed, problems=problems, values=form)
    place, item, amount, direction, reason, key = asked
    _, broken = refusal(
        session,
        lambda: record_adjustment(
            session,
            key,
            signed.admin.id,
            place.id,
            item.id,
            amount,
            found=direction == "found",
            reason_id=reason.id,
        ),
        WHOLE,
        COUNTABLE,
    )
    if broken is not None:
        log.info("adjustment refused by a rule", item_id=item.id, rule=broken)
        field = "location_id" if broken == COUNTABLE else "amount"
        return _counts_page(
            request, session, signed, problems={field: RULES[broken]}, values=form
        )
    session.commit()
    return redirect(COUNTS, notice="adjusted", section="adjust")
