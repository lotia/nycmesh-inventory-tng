"""Items and the packages they come in. An item's unit is fixed once stock
has moved in it or its amounts have been converted, or while a label of it
is printed and not revoked (unit_changes.unit_fixed): the form offers only
another length, for one in a length, and asks "Were the recorded amounts
really in m?" before it converts them (Ali, 2026-11-24, inventory-tng-y50b).
Answered No, it links to the guided recount (unit_recount_routes), or,
fixed only by labels, to the item's labels, to revoke them and correct a
mistake. A package's size fixed only by labels says so too
(items.sizes_fixed). A retired item with stock left may still be taken,
never received (docs/decisions/0003)."""

from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from typing import Annotated, NamedTuple

import structlog
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from sqlalchemy.orm import Session

from inventory_tng.admins.guards import AdminSignedIn, SignedIn
from inventory_tng.catalogue import (
    forms,
    items,
    queries,
    reading_rules,
    unit_change_forms,
    unit_changes,
    units,
)
from inventory_tng.catalogue.amount_rules import becomes_counted_whole, counted_whole
from inventory_tng.catalogue.items_listing import SEARCHED
from inventory_tng.catalogue.paths import CATEGORIES, ITEMS, READINGS, unit_recount
from inventory_tng.catalogue.reading_rules import NOT_A_UNIT
from inventory_tng.catalogue.unit_conversion import convertible
from inventory_tng.core.db_errors import refusal, refusal_naming_index
from inventory_tng.core.db_locking import stale_since
from inventory_tng.core.db_once_committed import once_committed
from inventory_tng.core.db_sessions import DbSession
from inventory_tng.core.local_time import site_zone
from inventory_tng.core.phrases import (
    Separator,
    Words,
    gettext,
    lazy_gettext,
    ngettext,
)
from inventory_tng.labels import labels
from inventory_tng.labels.paths import item_labels
from inventory_tng.models.base import INTEGER_MAX, whole
from inventory_tng.models.catalogue import (
    UNITS,
    Item,
    ItemAlias,
    ItemPackage,
    reading_rules_held,
)
from inventory_tng.models.labels import Label
from inventory_tng.models.names import canonical
from inventory_tng.models.stock import OpenPackage
from inventory_tng.stock import open_packages
from inventory_tng.stock.forms import now_counted_in
from inventory_tng.stock.ledger import tracks_opened
from inventory_tng.stock.open_packages_listing import (
    BY_OPENED,
    BY_PACKAGE,
    BY_SHOWS,
    OLDEST_FIRST,
)
from inventory_tng.stock.queries import item_total
from inventory_tng.web.admin_pages import (
    TextForm,
    admin_page,
    now,
    retirement,
    undo_retire,
)
from inventory_tng.web.decimal_separator import decimal_separator
from inventory_tng.web.forms import (
    ALERTS,
    CHANGED,
    RETIRED,
    RULES,
    WHOLE_UNITS,
    form_version,
)
from inventory_tng.web.listing import (
    UNASKED,
    Asked,
    AskedView,
    Listing,
    Sorting,
    View,
    in_order,
)
from inventory_tng.web.navigation import SECTION_ITEMS
from inventory_tng.web.pages import NOTICES, add_notices, not_found, redirect

log = structlog.get_logger(__name__)

router = APIRouter(include_in_schema=False)

# Refused by _change_or_note, as a rule would be, to ask about close names.
CLOSE = "close_names"
# The rules a change to an item may break, besides "unique", and the field
# each is shown by.
REFUSED_FIELDS = {
    "item_category_current": "category_id",
    items.MINIMUM_WHOLE: "minimum",
    **dict.fromkeys(unit_changes.UNIT_FIXED_RULES.values(), "unit"),
    unit_changes.UNIT_CHECKS_WAITING: "unit",
    unit_changes.UNIT_OUT_OF_RANGE: "unit",
}
# The item form's answer to "Were the recorded amounts really in m?"
# (unit_changes.UNIT_UNCONFIRMED): its button, Yes or No, and the unit it asked
# about, so an answer about another is asked again.
RECORDED = "unit_recorded"
RECORDED_IN = "unit_asked"


class _AskFirst(Exception):
    """A change whose name is close to other items': undone, to ask."""


def _change_or_note[T](
    session: Session,
    change: Callable[[], T],
    problems: dict[str, Words],
    fields: items.Fields,
    typed: forms.Typed,
    item_id: int | None = None,
    *,
    asked: list[items.Close] | None = None,
) -> tuple[T | None, str | None]:
    """Makes the change to `fields`: what it did, and None; or, if it
    breaks a unique index or one of REFUSED_FIELDS' rules, None, and the
    rule, which `problems` then says beside its field; or one of ALERTS;
    or, given `asked` and every rule allowing it, CLOSE when other items'
    names or aliases are close to its (docs/decisions/0021), those items
    put in `asked`, and nothing of it kept."""

    def asking() -> T:
        done = change()
        # Renamed to its own alias, it was found by that name already.
        swapped = isinstance(done, items.Renamed) and done.swapped is not None
        if asked is not None and not swapped:
            session.flush()
            # An add returns its item; a save is of item_id's.
            made = done.id if isinstance(done, Item) else item_id or 0
            if near := items.similar(session, fields.name, made):
                asked.extend(near)
                raise _AskFirst
        return done

    try:
        done, refused = refusal_naming_index(
            session,
            asking,
            "unique",
            items.NAME_IS_ALIAS,
            items.READ_ALIKE,
            items.PACKAGES_WHOLE,
            unit_changes.UNIT_UNCONFIRMED,
            *REFUSED_FIELDS,
            *ALERTS,
        )
    except _AskFirst:
        log.debug("close names asked about", item_id=item_id, found=len(asked or ()))
        return None, CLOSE
    broken = refused.rule if refused else None
    if refused is not None and broken in ("unique", items.NAME_IS_ALIAS):
        field, words = forms.taken(session, fields, typed, item_id, refused)
        problems[field] = words
    elif broken == items.READ_ALIKE:
        problems["name"] = forms.read_alike(session, typed)
    elif broken == items.PACKAGES_WHOLE:
        # Made a fraction after the form was checked: rare.
        fraction = forms.package_fraction(session, item_id) if item_id else None
        problems["unit"] = fraction or WHOLE_UNITS
    elif broken in REFUSED_FIELDS:
        if broken == items.MINIMUM_WHOLE:  # The form's own check let it by.
            log.warning("database refused a fractional reorder at", item_id=item_id)
        problems[REFUSED_FIELDS[broken]] = RULES[broken]
    return done, broken


PACKAGE_ADDED = lazy_gettext("Package added.")
PACKAGE_RETIRED = lazy_gettext("Package retired.")
PACKAGE_SAVED = lazy_gettext("Package saved.")
PACKAGE_RESTORED = lazy_gettext("Package brought back.")
PACKAGE_TRACKING = lazy_gettext("Saved: how its opened packages are followed.")
PACKAGE_SETTINGS = lazy_gettext(
    "Saved: when an open one is a short end, and how many sealed to keep."
)
TRACKING_ON = lazy_gettext(
    "Their opened packages are followed now, where a package does not say otherwise."
)
TRACKING_OFF = lazy_gettext(
    "Their opened packages become loose units now, where a package does not "
    "say otherwise."
)
TRACKING_NONE = lazy_gettext("Tick the items to change first.")
TRACKING_SAME = lazy_gettext(
    "Nothing changed: those ticked were set so already, or are retired."
)
ALIAS_ADDED = lazy_gettext("Alias added.")
RENAMED = lazy_gettext(
    "Renamed. The old name is kept as an alias so searches still find it; "
    "remove it below if it shouldn't be."
)
ALIAS_REMOVED = lazy_gettext("Alias removed.")
add_notices(
    {
        "package_added": PACKAGE_ADDED,
        "package_retired": PACKAGE_RETIRED,
        "package_saved": PACKAGE_SAVED,
        "package_restored": PACKAGE_RESTORED,
        "package_tracking": PACKAGE_TRACKING,
        "package_settings": PACKAGE_SETTINGS,
        "tracking_on": TRACKING_ON,
        "tracking_off": TRACKING_OFF,
        "tracking_none": TRACKING_NONE,
        "tracking_same": TRACKING_SAME,
        "tracking_changed": RULES[CHANGED],
        "alias_added": ALIAS_ADDED,
        "item_renamed": RENAMED,
        "alias_removed": ALIAS_REMOVED,
    }
)

# What "Same as the item", "Track" and "Don't track" send for a package.
TRACKING = {"": None, "on": True, "off": False}


def made_loose(count: int) -> str:
    """After turning following off made what was left on `count` open
    packages loose units (open_packages.unfollowing), said after the page's
    own notice: never called closing, which is for write-offs, and no
    amounts or places (Ali, 2026-11-24, inventory-tng-j89e)."""
    return ngettext(
        "What was left on %(num)s open package is now loose units, where it was.",
        "What was left on %(num)s open packages is now loose units, where each "
        "one was.",
        count,
    )


def followed_now(count: int) -> str:
    """The hint under a setting that would make what is left on `count`
    open packages, followed now, loose units if turned off (Ali,
    2026-11-24, inventory-tng-j89e)."""
    return ngettext(
        "%(num)s open package is followed now. Turning this off makes what's "
        "left on it loose units.",
        "%(num)s open packages are followed now. Turning this off makes what's "
        "left on them loose units.",
        count,
    )


# The notices of the changes that may make open packages loose (_loose_said).
MAY_MAKE_LOOSE = ("saved", "item_renamed", "package_tracking", "tracking_off")


def _loose_said(request: Request) -> dict[str, str]:
    """The page's notice and, after it, made_loose for the open packages
    its address says a change made loose; nothing to add if none, or if
    no such change says so."""
    said = request.query_params.get("notice", "")
    count = whole(request.query_params.get("loose", ""), 1, INTEGER_MAX)
    if said not in MAY_MAKE_LOOSE or count is None:
        return {}
    return {"notice": f"{NOTICES[said]} {made_loose(count)}"}


def _loose_query(count: int) -> dict[str, str]:
    """What a redirect's address says of the open packages a change made
    loose, for _loose_said."""
    return {"loose": str(count)} if count else {}


def _listed(
    asked: AskedView,
    q: str = "",
    show: str = "",
    pick: str = "",
    category: str = "",
) -> Listing[Item]:
    """The items list as its query string asks for it, with its filter by
    category (Ali, 2026-11-15): one not a number is none."""
    category_id = whole(category, 1, INTEGER_MAX)
    return SEARCHED.read(
        q,
        show,
        pick,
        asked,
        filters={"category": str(category_id or "")},
        where=[Item.category_id == category_id] if category_id else (),
    )


Listed = Annotated[Listing[Item], Depends(_listed)]


def _name_field(session: Session, similar: Sequence[items.Close]) -> dict[str, object]:
    """What catalogue/_item_name.html and catalogue/_units_read.html need
    besides the form's values: units_read, what may be units in a name as
    typed, as the reading rules read them, and shown_as, how a name typed
    is shown where that is not as saved (units.shown_name, Ali,
    2026-11-15), read as the form sent answers it, as saving it would
    (inventory-tng-nbo)."""
    rules = reading_rules_held(session)

    def units_read(typed: str) -> list[units.Found]:
        # One per unit as typed, as one answer reads them all.
        once: dict[str, units.Found] = {}
        for each in units.found_in(canonical(typed.strip()), rules):
            once.setdefault(each.key, each)
        return list(once.values())

    def shown_as(typed: str, sent: Mapping[str, str]) -> str | None:
        read = forms.read(typed, rules, sent)
        shown = units.shown_name(read.name, read.rules)
        return shown if shown != read.name else None

    return {
        "similar": similar,
        "items_path": ITEMS,
        "different": forms.DIFFERENT,
        "units_read": units_read,
        "shown_as": shown_as,
        "reading_field": forms.READING,
        "not_a_unit": NOT_A_UNIT,
    }


def _list_page(
    request: Request,
    session: Session,
    signed: SignedIn,
    *,
    listing: Listing[Item] | None = None,
    problems: Mapping[str, Words] | None = None,
    values: Mapping[str, str] | None = None,
    undo: str | None = None,
    similar: Sequence[items.Close] = (),
) -> HTMLResponse:
    listing = (listing or SEARCHED.read()).find(session)
    categories = queries.categories_by_name(session)
    return admin_page(
        request,
        signed,
        "catalogue/items.html",
        {
            "title": SECTION_ITEMS,
            "undo": undo,
            "here": ITEMS,
            "categories_path": CATEGORIES,
            "readings_path": READINGS,
            "listing": listing,
            "categories": [c for c in categories if c.retired_at is None],
            "all_categories": categories,
            "category_names": {c.id: c.name for c in categories},
            "units": UNITS,
            "problems": problems,
            "values": values,
            **_name_field(session, similar),
            **_loose_said(request),
        },
    )


# The item page's section of open packages, which its headings' links go
# back to.
OPEN_PACKAGES = "open-packages"
# How an item's open packages sort: oldest first, the first reported used
# up before the rest, as they always have; or by a heading (Ali,
# 2026-11-18, inventory-tng-t150). The only list on the page that sorts,
# so its query string's sort and order are its own.
OPEN_SORTING = Sorting(
    "oldest",
    {
        "oldest": OLDEST_FIRST,
        "package": BY_PACKAGE,
        "left": BY_SHOWS,
        "opened": BY_OPENED,
    },
)


def _short_fraction(
    session: Session,
    item: Item,
    problems: Mapping[str, Words] | None,
    values: Mapping[str, str] | None,
) -> int | None:
    """The package whose Too short to use box a switch to each refused for
    its fraction is outlined in, as the words beside the unit do not name
    it (Ali, 2026-11-23, inventory-tng-fa5i); None for any other page."""
    unit = (values or {}).get("unit", "")
    switching = becomes_counted_whole(item.unit, unit)
    if not switching or "unit" not in (problems or {}):
        return None
    package = items.fractional_package(session, item.id)
    if package is None or forms.size_has_fraction(package):
        return None
    return package.id


def _short_shown(
    item: Item,
    packages: Sequence[ItemPackage],
    short_fraction: int | None,
    problems: Mapping[str, Words] | None,
    values: Mapping[str, str] | None,
) -> set[int]:
    """The packages whose Too short to use box the item page shows: those
    whose opened ones are followed, as it only says when an open package is
    a short end (Ali, 2026-11-23, inventory-tng-j89e); one whose box is
    to be set whole, outlined, so it can be (inventory-tng-fa5i); and one
    a resize was refused for, at or below its length, so it can be
    changed first. The value of one hidden is kept."""
    each = counted_whole(item.unit)
    resized = None
    if (problems or {}).get("size") is RULES[SHORT_UNDER_SIZE]:
        resized = (values or {}).get("package_id")
    return {
        package.id
        for package in packages
        if tracks_opened(item.track_opened, package.track_opened)
        or package.id == short_fraction
        or (each and forms.short_has_fraction(package))
        or str(package.id) == resized
    }


class FollowedNow(NamedTuple):
    """The hints of open packages followed now (followed_now): under the
    item's box, or None, and under each package's own setting, by id."""

    item: str | None
    packages: dict[int, str]


def _followed_now(
    item: Item, packages: Sequence[ItemPackage], opened: Sequence[open_packages.Shown]
) -> FollowedNow:
    """The item's hint counts only the open packages that follow its own
    setting, their package set "Same as the item"; a package's counts its
    own open ones while they are followed. None where none would be made
    loose (Ali, 2026-11-24, inventory-tng-j89e)."""
    by_package = Counter(each.row.package_id for each in opened)
    as_item = sum(
        by_package[package.id] for package in packages if package.track_opened is None
    )
    return FollowedNow(
        followed_now(as_item) if item.track_opened and as_item else None,
        {
            package.id: followed_now(by_package[package.id])
            for package in packages
            if by_package[package.id]
            and tracks_opened(item.track_opened, package.track_opened)
        },
    )


class FixedBy(NamedTuple):
    """Why an item's unit is fixed, if it is, and its labels not revoked,
    its own and its packages', read together."""

    why: unit_changes.UnitFixed | None
    printed: Sequence[labels.Named]


def _fixed_by(session: Session, item: Item) -> FixedBy:
    """Why the item's unit is fixed, and its labels: labels revoked
    between the two reads fix nothing, so no hint or note says "0
    labels"."""
    why = unit_changes.unit_fixed(session, item)
    printed = labels.current_of(session, Label.item_id == item.id)
    return FixedBy(None if why == "labelled" and not printed else why, printed)


def _item_page(
    request: Request,
    session: Session,
    signed: SignedIn,
    item: Item,
    *,
    problems: Mapping[str, Words] | None = None,
    values: Mapping[str, str] | None = None,
    alert: Words | None = None,
    similar: Sequence[items.Close] = (),
    undo: str | None = None,
    asked: Asked = UNASKED,
    held_back: ItemPackage | None = None,
    short_to_set: ItemPackage | None = None,
    asking_unit: bool = False,
    unit_declined: unit_changes.UnitFixed | None = None,
    unit_declined_to: str | None = None,
    unit_example: queries.UnitChangeExample | None = None,
    fixed_by: FixedBy | None = None,
    status_code: int | None = None,
) -> HTMLResponse:
    """The item's page; its open packages sorted as `asked`, all of them.
    `asking_unit`, the form sent back asks whether what was recorded was
    in its unit, Yes and No in place of Save (unit_changes.UNIT_UNCONFIRMED).
    With `held_back`, a retired package not brought back for its size's
    fraction: said above the page, with a link to add a new package. With
    `short_to_set`, a package brought back whose Too short to use box is
    outlined, the whole numbers it may be beside it. `unit_declined`, why
    the unit is fixed when its question was answered No, to change it to
    `unit_declined_to`: labels, and the form links to revoke them; or
    stock moved or its amounts converted, and it links to the guided
    recount (unit_recounts), saying what it does: `unit_example`, the
    amount the note gives as an example (queries.unit_change_example), if
    any. A retired item's No leaves it unset: its note links nothing.
    `fixed_by`, why its unit is fixed and its labels as the note read
    them, so the page says the same; read here if not given. Its status
    is `status_code`, if given, else as admin_page says."""
    here = f"{ITEMS}/{item.id}"
    packages = queries.packages_of_item(session, item.id)
    view = View.read(here, OPEN_SORTING, asked, anchor=OPEN_PACKAGES)
    opened = in_order(
        open_packages.shown(session, OpenPackage.item_id == item.id), view
    )
    log.debug("open packages listed", item_id=item.id, found=len(opened), sort=view.by)
    short_fraction = _short_fraction(session, item, problems, values)
    fixed, printed = fixed_by or _fixed_by(session, item)
    by_package = Counter(each.label.package_id for each in printed)
    # Only a current one's size can change.
    current = [each.id for each in packages if each.retired_at is None]
    sizes = {
        each: why
        for each, why in items.sizes_fixed(session, current).items()
        if why == "used" or by_package[each]
    }
    return admin_page(
        request,
        signed,
        "catalogue/item.html",
        {
            "title": item.name_shown,
            "here": here,
            "back_link": (ITEMS, SECTION_ITEMS),
            "item": item,
            **retirement(session, item),
            "packages": packages,
            "holds": forms.HOLDS,
            "aliases": items.aliases(session, item.id),
            "sizes_fixed": sizes,
            "undo": undo,
            "categories": [
                c for c in queries.categories_by_name(session) if c.retired_at is None
            ],
            "units": unit_changes.units_open(item.unit, fixed),
            "unit_fixed": fixed,
            "asking_unit": asking_unit,
            "unit_declined": unit_declined,
            "recorded": RECORDED,
            "recorded_in": RECORDED_IN,
            "its_labels": item_labels(item.id),
            "recount_to": unit_declined_to,
            "recount_in": unit_declined_to and unit_recount(item.id, unit_declined_to),
            "unit_example": unit_example,
            # Converted before: the recount asks its packages' sizes again.
            "recount_resizes": unit_declined == "converted"
            and any(each.unit == item.unit for each in packages),
            "in_stock": item_total(session, item.id),
            "labels": printed,
            "labels_printed": len(printed),
            "package_labels_printed": by_package,
            "open_packages": opened,
            "open_view": view,
            "zone": site_zone(session),
            "problems": problems,
            "values": values,
            "short_fraction": short_fraction,
            "alert": alert,
            "held_back": held_back,
            "short_to_set": short_to_set,
            "set_whole": short_to_set and forms.short_to_set(short_to_set),
            "short_shown": _short_shown(
                item, packages, short_fraction, problems, values
            ),
            "followed_now": _followed_now(item, packages, opened),
            **_name_field(session, similar),
            **_loose_said(request),
        },
        status_code=409 if held_back else status_code,
    )


def _item_changed(
    request: Request,
    session: Session,
    signed: SignedIn,
    item_id: int,
    change: Callable[[], object],
    notice: str,
    *,
    section: str = "packages",
    **query: str,
) -> Response:
    """A change to an item's package or alias, made and committed, then the
    item page saying `notice` by `section`; refused, said above the page,
    while the item is retired."""
    _, broken = refusal(session, change, RETIRED)
    if broken:
        found = session.get_one(Item, item_id)
        return _item_page(request, session, signed, found, alert=RULES[RETIRED])
    session.commit()
    return redirect(f"{ITEMS}/{item_id}", notice=notice, section=section, **query)


def _deciding[T](
    session: Session, typed: forms.Typed, signed: SignedIn, change: Callable[[], T]
) -> Callable[[], T]:
    """`change`, after the answers it was asked keep as reading rules, in
    its savepoint: refused, it keeps neither."""

    def deciding() -> T:
        if typed.answered:
            written = reading_rules.decide(session, typed.answered, signed.admin.id)
            typed.decided[:] = [rule.id for rule in written]
        return change()

    return deciding


def _decided(session: Session, typed: forms.Typed, **ids: int) -> None:
    """Log the reading rules a form wrote, once committed."""
    if typed.decided:
        once_committed(
            session,
            log.info,
            "reading rules set",
            reading_rule_ids=typed.decided,
            by="form",
            **ids,
        )


@router.get(ITEMS)
def list_items(
    request: Request,
    signed: AdminSignedIn,
    session: DbSession,
    listing: Listed,
    undo: str = "",
) -> Response:
    back = undo_retire(session, Item, undo, ITEMS)
    return _list_page(request, session, signed, listing=listing, undo=back)


@router.get(f"{ITEMS}/suggest")
def suggest_items(session: DbSession, listing: Listed) -> JSONResponse:
    """What the search box offers as one types (_listing.html)."""
    names = {c.id: c.name for c in queries.categories_by_name(session)}
    offered = listing.suggest(session, lambda item: names.get(item.category_id, ""))
    return JSONResponse(offered, headers={"Cache-Control": "no-store"})


@router.post(ITEMS)
def add_item(
    request: Request, signed: AdminSignedIn, session: DbSession, form: TextForm
) -> Response:
    form = forms.add_form(form)
    fields, problems, typed = forms.item_fields(
        session, form, separator=decimal_separator(request)
    )
    item = None
    if fields is not None:
        asked = None if forms.confirmed(session, form, fields) else []
        adding = _deciding(session, typed, signed, lambda: items.add(session, fields))
        item, broken = _change_or_note(
            session, adding, problems, fields, typed, asked=asked
        )
        if broken == CLOSE:
            return _list_page(
                request, session, signed, values=form, similar=asked or ()
            )
    if item is None:
        return _list_page(request, session, signed, problems=problems, values=form)
    _decided(session, typed, item_id=item.id)
    session.commit()
    return redirect(f"{ITEMS}/{item.id}", notice="added")


# Before the routes of one item, whose id would take "tracking".
@router.post(f"{ITEMS}/tracking")
def track_items(
    signed: AdminSignedIn,
    session: DbSession,
    listing: Listed,
    item: Annotated[list[int], Form()] = [],  # noqa: B006  FastAPI copies it.
    track: Annotated[str, Form()] = "",
) -> Response:
    """Set "track each opened package" on the items ticked, on the page
    shown, and come back to it as it was: the list's view comes in the
    form's address. Turned off, what is left on the open packages no
    longer followed is made loose (open_packages.unfollowing)."""
    back = listing.link()
    if TRACKING.get(track) is None:
        return not_found()
    if not item:
        return redirect(back, notice="tracking_none")
    done, broken = refusal(
        session,
        lambda: open_packages.unfollowing(
            session,
            signed.admin.id,
            now(),
            lambda: items.track_items(session, item, track == "on"),
            # Only turning it off can leave open packages not followed.
            item_ids=item if track == "off" else (),
        ),
        CHANGED,
    )
    if done is None:
        log.info("package tracking refused", rule=broken, scope="bulk")
        return redirect(back, notice="tracking_changed")
    changed, loose = done
    session.commit()
    # Ticked items set so already may still have had open packages made loose.
    if not changed and not loose:
        return redirect(back, notice="tracking_same")
    return redirect(back, notice=f"tracking_{track}", **_loose_query(loose))


@router.get(f"{ITEMS}/{{item_id}}")
def item(
    request: Request,
    signed: AdminSignedIn,
    session: DbSession,
    item_id: int,
    asked: AskedView,
    undo: str = "",
    short: str = "",
) -> Response:
    found = session.get(Item, item_id)
    if found is None:
        return not_found()
    # A package just retired, offered back.
    back = undo_retire(session, ItemPackage, undo, f"{ITEMS}/{item_id}/packages")
    return _item_page(
        request,
        session,
        signed,
        found,
        undo=back,
        asked=asked,
        short_to_set=_short_to_set(session, found, whole(short, 1, INTEGER_MAX)),
    )


def _short_to_set(
    session: Session, item: Item, package_id: int | None
) -> ItemPackage | None:
    """The package, just brought back, if it is current and too short to
    use below a fraction on an item counted each: its box is outlined to
    be set whole (Ali, 2026-11-23, inventory-tng-fa5i). Read on each
    show, so a reload once it is set whole outlines nothing."""
    if package_id is None or not counted_whole(item.unit):
        return None
    package = _package(session, item.id, package_id)
    if package is None or package.retired_at is not None:
        return None
    if not forms.short_has_fraction(package):
        return None
    log.debug("package short end to set", item_package_id=package.id)
    return package


@router.post(f"{ITEMS}/{{item_id}}")
def save_item(
    request: Request,
    signed: AdminSignedIn,
    session: DbSession,
    item_id: int,
    form: TextForm,
) -> Response:
    found = session.get(Item, item_id)
    if found is None:
        return not_found()
    fields, problems, typed = forms.item_fields(
        session, form, found, separator=decimal_separator(request)
    )
    renamed = None
    answer = form.get(RECORDED)
    if fields is not None and answer == "no":
        return _unit_declined(request, session, signed, found, form, fields.unit)
    if fields is not None:
        seen = form_version(form.get("version", ""))
        recorded_in = form.get(RECORDED_IN) if answer == "yes" else None
        same = forms.same_name(session, found, form, fields, typed)
        asked = None if same else []
        loose = 0

        def saving() -> items.Renamed | None:
            nonlocal loose
            # The places first: the answers' reading rules lock the items
            # they read again FOR UPDATE, which a save at those places
            # holds after them (open_packages.unfollowing).
            renamed, loose = open_packages.unfollowing(
                session,
                signed.admin.id,
                now(),
                _deciding(
                    session,
                    typed,
                    signed,
                    lambda: items.save(
                        session, found, fields, seen=seen, recorded_in=recorded_in
                    ),
                ),
                item_ids=() if fields.track_opened else [item_id],
            )
            return renamed

        renamed, broken = _change_or_note(
            session, saving, problems, fields, typed, item_id, asked=asked
        )
        if broken == CLOSE:
            return _item_page(
                request, session, signed, found, values=form, similar=asked or ()
            )
        if broken in ALERTS:
            alert = RULES[broken]
            return _item_page(request, session, signed, found, alert=alert)
        if broken == unit_changes.UNIT_UNCONFIRMED and not problems:
            return _item_page(
                request, session, signed, found, values=form, asking_unit=True
            )
    if problems:
        return _item_page(
            request, session, signed, found, problems=problems, values=form
        )
    _decided(session, typed, item_id=item_id)
    session.commit()
    said = _loose_query(loose)
    if renamed is None:
        return redirect(f"{ITEMS}/{item_id}", notice="saved", **said)
    return redirect(
        f"{ITEMS}/{item_id}", notice="item_renamed", section="aliases", **said
    )


def _unit_declined(
    request: Request,
    session: Session,
    signed: SignedIn,
    item: Item,
    form: Mapping[str, str],
    unit: str,
) -> Response:
    """No to "Were the recorded amounts really in m?": nothing saved, the
    form shown again in its unit as it is, saying what to do instead; for
    a retired item, which can't be changed until it is brought back, only
    that the unit is unchanged, linking nothing (Ali, 2026-11-25)."""
    # Read once: a label revoked meanwhile never makes the page's count
    # differ from the note's.
    why, printed = fixed_by = _fixed_by(session, item)
    if why is None or not convertible(item.unit, unit):
        # Answered on a page drawn before the item changed, its labels
        # revoked since, or about a change that is not between lengths:
        # ask afresh.
        return _item_page(
            request, session, signed, item, values=form, fixed_by=fixed_by
        )
    log.info("unit change declined", item_id=item.id, reason=why)
    values = {**form, "unit": item.unit}
    example = queries.unit_change_example(session, item, unit, why)
    retired = item.retired_at is not None
    problems = {
        "unit": unit_change_forms.unit_declined_problem(
            item.unit, unit, why, example, len(printed), retired
        )
    }
    return _item_page(
        request,
        session,
        signed,
        item,
        problems=problems,
        values=values,
        unit_declined=None if retired else why,
        unit_declined_to=unit,
        unit_example=example,
        fixed_by=fixed_by,
    )


@router.post(f"{ITEMS}/{{item_id}}/retire")
def retire_item(session: DbSession, item_id: int) -> Response:
    found = session.get(Item, item_id)
    if found is None:
        return not_found()
    items.retire(session, found, now())
    session.commit()
    return redirect(ITEMS, notice="retired", undo=str(item_id))


@router.post(f"{ITEMS}/{{item_id}}/restore")
def restore_item(
    request: Request, signed: AdminSignedIn, session: DbSession, item_id: int
) -> Response:
    found = session.get(Item, item_id)
    if found is None:
        return not_found()
    # Its name and codes are its own still: unique, retired ones too.
    _, broken = refusal(
        session, lambda: items.restore(session, found), "item_category_current"
    )
    # Beside the name: the category list shows only current categories.
    if broken:
        problem = RULES[broken]
        return _item_page(request, session, signed, found, problems={"name": problem})
    session.commit()
    return redirect(f"{ITEMS}/{item_id}", notice="restored")


def _package(session: Session, item_id: int, package_id: int) -> ItemPackage | None:
    """The package a path names, if it is of the item it names."""
    package = session.get(ItemPackage, package_id)
    return package if package is not None and package.item_id == item_id else None


# A size at or below its too-short length (models.catalogue.ItemPackage).
SHORT_UNDER_SIZE = "ck_item_package_short_below_under_size"
# The unit a package form showed its amount in, the item's to add one or
# the package's for its too-short length, compared as it is saved
# (unit_changes.require_unit_shown); the package's for its size, to say a
# form refused as changed since was so as its unit changed.
UNIT_SHOWN = "unit_shown"


def _asked_in_unit_now(
    problems: dict[str, Words],
    form: Mapping[str, str],
    box: str,
    unit: str,
    *,
    required: Words | None = None,
    separator: Separator,
) -> None:
    """A package form drawn again, for whatever reason, its amount in `box`
    typed in a unit other than `unit`, the one it is in now: said beside
    it, so the number is checked, not saved again in the new unit as the
    form now shows it. Anything else wrong with it in any unit is said
    after, so both are fixed at once; what depends on the unit is not, as
    the number is to be typed again in the new one (inventory-tng-hhlr)."""
    if form.get(UNIT_SHOWN, "") == unit:
        return
    said = now_counted_in(unit)
    other = forms.unit_free_problem(
        form.get(box, ""), required=required, separator=separator
    )
    if other is None:
        problems[box] = said
    else:
        # Translators: %(changed)s says the item is now counted in another
        # unit, %(problem)s what else is wrong with the number typed.
        problems[box] = gettext("%(changed)s %(problem)s", changed=said, problem=other)


@router.post(f"{ITEMS}/{{item_id}}/packages")
def add_package(
    request: Request,
    signed: AdminSignedIn,
    session: DbSession,
    item_id: int,
    form: TextForm,
    package_name: Annotated[str, Form()] = "",
    size: Annotated[str, Form()] = "",
) -> Response:
    found = session.get(Item, item_id)
    if found is None:
        return not_found()
    typed = forms.typed_name(session, package_name, form)
    name = typed.name
    separator = decimal_separator(request)
    parsed, problems = forms.package_fields(
        found, name, typed.asked, size, separator=separator
    )
    if parsed is not None and not problems:
        package, broken = refusal(
            session,
            _deciding(
                session,
                typed,
                signed,
                lambda: items.add_package(
                    session, found, name, parsed, shown_unit=form.get(UNIT_SHOWN, "")
                ),
            ),
            "unique",
            RETIRED,
            items.READ_ALIKE,
            items.PACKAGE_WHOLE,
            unit_changes.UNIT_CHANGED,
        )
        if broken == RETIRED:
            return _item_page(request, session, signed, found, alert=RULES[RETIRED])
        if package is not None:
            _decided(session, typed, item_package_id=package.id)
            session.commit()
            return redirect(
                f"{ITEMS}/{item_id}", notice="package_added", section="packages"
            )
        if broken == items.PACKAGE_WHOLE:  # Its item made each meanwhile.
            problems["size"] = WHOLE_UNITS
        elif broken == unit_changes.UNIT_CHANGED:  # Asked again in its unit now.
            problems["size"] = now_counted_in(found.unit)
        else:
            problems["package_name"] = (
                forms.read_alike(session, typed)
                if broken == items.READ_ALIKE
                else forms.PACKAGE_TAKEN
            )
    _asked_in_unit_now(
        problems,
        form,
        "size",
        found.unit,
        required=forms.SIZE_REQUIRED,
        separator=separator,
    )
    # With the units chosen, if any: not a package's own form's (package_id).
    values = {**form, "package_name": name, "size": size}
    return _item_page(request, session, signed, found, problems=problems, values=values)


@router.post(f"{ITEMS}/{{item_id}}/packages/{{package_id}}")
def save_package(
    request: Request,
    signed: AdminSignedIn,
    session: DbSession,
    item_id: int,
    package_id: int,
    form: TextForm,
    package_name: Annotated[str, Form()] = "",
    size: Annotated[str, Form()] = "",
    package_version: Annotated[str, Form()] = "",
) -> Response:
    """A package renamed, or resized while none of it has moved."""
    package = _package(session, item_id, package_id)
    if package is None:
        return not_found()
    found = session.get_one(Item, item_id)
    typed = forms.typed_name(session, package_name, form)
    name = typed.name
    separator = decimal_separator(request)
    parsed, problems = forms.package_fields(
        found, name, typed.asked, size, kept=package.size, separator=separator
    )
    # Beside the fields of the package it was sent for, with the units
    # chosen, if any.
    values = {
        **form,
        "package_id": str(package_id),
        "package_name": name,
        "size": size,
        "package_version": package_version,
    }
    seen = form_version(package_version)
    # Changed since it was shown: said before what is wrong with what was
    # typed, so the admin learns it once (inventory-tng-hnta). Not found
    # if retired, as the save would be; checked again as it saves.
    if stale_since(session, package, seen, current=True):
        if form.get(UNIT_SHOWN, package.unit) == package.unit or (
            not unit_changes.only_unit_changed(session, package, seen)
        ):
            return _item_page(request, session, signed, found, alert=RULES[CHANGED])
        # Only its item's unit switched, which moves its packages' versions
        # (inventory-tng-74f3): asked again in the new unit, as the other
        # package forms ask, keeping what was typed, at the version as
        # locked, which only_unit_changed's history covers; a change after
        # it is refused as the form is sent again.
        _asked_in_unit_now(
            problems,
            form,
            "size",
            package.unit,
            required=forms.SIZE_REQUIRED,
            separator=separator,
        )
        return _item_page(
            request,
            session,
            signed,
            found,
            problems=problems,
            values=values | {"package_version": str(package.edit_version)},
            status_code=409,
        )
    if parsed is not None and not problems:
        _, broken = refusal(
            session,
            _deciding(
                session,
                typed,
                signed,
                lambda: items.save_package(session, package, name, parsed, seen=seen),
            ),
            items.READ_ALIKE,
            "unique",
            *items.SIZE_FIXED_RULES.values(),
            SHORT_UNDER_SIZE,
            items.PACKAGE_WHOLE,
            *ALERTS,
        )
        # Its size field is gone once fixed: said above the page, as for a
        # form that went stale.
        if broken in {*ALERTS, *items.SIZE_FIXED_RULES.values()}:
            return _item_page(request, session, signed, found, alert=RULES[broken])
        if broken is None:
            _decided(session, typed, item_package_id=package_id)
            session.commit()
            return redirect(
                f"{ITEMS}/{item_id}", notice="package_saved", section="packages"
            )
        if broken == "unique":
            problems["package_name"] = forms.PACKAGE_TAKEN
        elif broken == items.READ_ALIKE:
            problems["package_name"] = forms.read_alike(session, typed)
        else:  # SHORT_UNDER_SIZE, or PACKAGE_WHOLE: its item made each.
            problems["size"] = RULES[broken]
    return _item_page(request, session, signed, found, problems=problems, values=values)


@router.post(f"{ITEMS}/{{item_id}}/packages/{{package_id}}/restore")
def restore_package(
    request: Request,
    signed: AdminSignedIn,
    session: DbSession,
    item_id: int,
    package_id: int,
) -> Response:
    package = _package(session, item_id, package_id)
    if package is None:
        return not_found()
    _, broken = refusal(
        session,
        lambda: items.restore_package(session, package),
        RETIRED,
        items.PACKAGE_WHOLE,
    )
    found = session.get_one(Item, item_id)
    if broken is None:
        session.commit()
        to_set = _short_to_set(session, found, package_id)
        short = {"short": str(package_id)} if to_set else {}
        return redirect(
            f"{ITEMS}/{item_id}", notice="package_restored", section="packages", **short
        )
    # Nor holding a fraction, to an item counted each: it has no box to
    # change while retired, so a new package is added instead (Ali,
    # 2026-11-23, inventory-tng-fa5i).
    if broken == items.PACKAGE_WHOLE:
        return _item_page(request, session, signed, found, held_back=package)
    return _item_page(request, session, signed, found, alert=RULES[RETIRED])


@router.post(f"{ITEMS}/{{item_id}}/packages/{{package_id}}/retire")
def retire_package(
    request: Request,
    signed: AdminSignedIn,
    session: DbSession,
    item_id: int,
    package_id: int,
) -> Response:
    package = _package(session, item_id, package_id)
    if package is None:
        return not_found()
    return _item_changed(
        request,
        session,
        signed,
        item_id,
        lambda: items.retire_package(session, package, now()),
        "package_retired",
        undo=str(package_id),
    )


@router.post(f"{ITEMS}/{{item_id}}/packages/{{package_id}}/tracking")
def track_package(
    request: Request,
    signed: AdminSignedIn,
    session: DbSession,
    item_id: int,
    package_id: int,
    track: Annotated[str, Form()] = "",
) -> Response:
    """A package's own "track each opened package", or as its item says.
    No longer followed, what is left on its open packages is made loose
    (open_packages.unfollowing)."""
    package = _package(session, item_id, package_id)
    if package is None or track not in TRACKING:
        return not_found()
    done, broken = refusal(
        session,
        lambda: open_packages.unfollowing(
            session,
            signed.admin.id,
            now(),
            lambda: items.track_package(session, package, TRACKING[track]),
            package_ids=() if TRACKING[track] else [package_id],
        ),
        RETIRED,
        CHANGED,
    )
    if done is None:
        found = session.get_one(Item, item_id)
        return _item_page(request, session, signed, found, alert=RULES[str(broken)])
    session.commit()
    return redirect(
        f"{ITEMS}/{item_id}",
        notice="package_tracking",
        section="packages",
        **_loose_query(done[1]),
    )


@router.post(f"{ITEMS}/{{item_id}}/packages/{{package_id}}/settings")
def package_settings(
    request: Request,
    signed: AdminSignedIn,
    session: DbSession,
    item_id: int,
    package_id: int,
    form: TextForm,
) -> Response:
    """A package's too-short-to-use length and minimum sealed, each blank
    for none; the length kept as it is where the page has no box for it
    (_short_shown), so none is sent."""
    short_below = form.get("short_below")
    minimum_sealed = form.get("minimum_sealed", "")
    package = _package(session, item_id, package_id)
    if package is None:
        return not_found()
    found = session.get_one(Item, item_id)
    separator = decimal_separator(request)
    short, minimum, problems = forms.package_settings_fields(
        package, found.unit, short_below, minimum_sealed, separator=separator
    )
    # Beside the fields of the package it was sent for.
    values = {
        "package_id": str(package_id),
        "minimum_sealed": minimum_sealed,
    }
    if short_below is not None:
        values["short_below"] = short_below
    if not problems:
        _, broken = refusal(
            session,
            lambda: items.package_settings(
                session,
                package,
                short,
                minimum,
                shown_unit=form.get(UNIT_SHOWN, ""),
                keep_short=short_below is None,
            ),
            RETIRED,
            SHORT_UNDER_SIZE,
            items.PACKAGE_WHOLE,
            unit_changes.UNIT_CHANGED,
        )
        if broken == RETIRED:
            return _item_page(request, session, signed, found, alert=RULES[RETIRED])
        if not broken:
            session.commit()
            return redirect(
                f"{ITEMS}/{item_id}", notice="package_settings", section="packages"
            )
        # Resized meanwhile to no more than it, its item made each, or its
        # unit changed: said beside it, as above.
        problems["short_below"] = {
            items.PACKAGE_WHOLE: WHOLE_UNITS,
            unit_changes.UNIT_CHANGED: now_counted_in(package.unit),
        }.get(broken, forms.SHORTER_THAN_WHOLE)
    if short_below and short_below.strip():  # A length typed, in a unit.
        _asked_in_unit_now(
            problems, form, "short_below", package.unit, separator=separator
        )
    return _item_page(request, session, signed, found, problems=problems, values=values)


@router.post(f"{ITEMS}/{{item_id}}/aliases")
def add_alias(
    request: Request,
    signed: AdminSignedIn,
    session: DbSession,
    item_id: int,
    form: TextForm,
    alias_name: Annotated[str, Form()] = "",
) -> Response:
    """Another name the item is found by, spelt as its name would be."""
    found = session.get(Item, item_id)
    if found is None:
        return not_found()
    typed = forms.typed_name(session, alias_name, form)
    name = typed.name
    problem = forms.item_name_problem(name, typed.asked, item_id)
    if problem is None:
        alias, broken = refusal(
            session,
            _deciding(
                session, typed, signed, lambda: items.add_alias(session, found, name)
            ),
            items.READ_ALIKE,
            "unique",
            items.ALIAS_IS_NAME,
            RETIRED,
        )
        if broken == RETIRED:
            return _item_page(request, session, signed, found, alert=RULES[RETIRED])
        if alias is not None:
            _decided(session, typed, item_alias_id=alias.id)
            session.commit()
            return redirect(
                f"{ITEMS}/{item_id}", notice="alias_added", section="aliases"
            )
        problem = (
            forms.read_alike(session, typed)
            if broken == items.READ_ALIKE
            else forms.alias_problem(session, found, typed, broken)
        )
    values = {**form, "alias_name": name}
    problems = {"alias_name": problem}
    return _item_page(request, session, signed, found, problems=problems, values=values)


@router.post(f"{ITEMS}/{{item_id}}/aliases/{{alias_id}}/remove")
def remove_alias(
    request: Request,
    signed: AdminSignedIn,
    session: DbSession,
    item_id: int,
    alias_id: int,
) -> Response:
    """An alias removed: its name is free again."""
    alias = session.get(ItemAlias, alias_id)
    if alias is None or alias.item_id != item_id:
        return not_found()
    return _item_changed(
        request,
        session,
        signed,
        item_id,
        lambda: items.remove_alias(session, alias, now()),
        "alias_removed",
        section="aliases",
    )
