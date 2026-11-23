"""Items and the packages they come in. An item's unit is fixed once stock
has moved in it (items.save): the form shows it, and does not offer to
change it. A retired item with stock left may still be taken, never
received (docs/decisions/0003)."""

from collections.abc import Callable, Mapping, Sequence
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from sqlalchemy.orm import Session

from inventory_tng.admins.guards import AdminSignedIn, SignedIn
from inventory_tng.catalogue import forms, items, queries, reading_rules, units
from inventory_tng.catalogue.items_listing import SEARCHED
from inventory_tng.catalogue.paths import CATEGORIES, ITEMS, READINGS
from inventory_tng.catalogue.reading_rules import NOT_A_UNIT
from inventory_tng.core.db_errors import refusal, refusal_naming_index
from inventory_tng.core.db_once_committed import once_committed
from inventory_tng.core.db_sessions import DbSession, exists_where
from inventory_tng.core.local_time import site_zone
from inventory_tng.core.phrases import Words, lazy, lazy_gettext
from inventory_tng.labels import labels
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
from inventory_tng.models.stock import Movement, OpenPackage
from inventory_tng.stock import open_packages
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
from inventory_tng.web.forms import ALERTS, RETIRED, RULES, WHOLE_UNITS, form_version
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
from inventory_tng.web.pages import add_notices, not_found, redirect

log = structlog.get_logger(__name__)

router = APIRouter(include_in_schema=False)

# Refused by _change_or_note, as a rule would be, to ask about close names.
CLOSE = "close_names"
# The rules a change to an item may break, besides "unique", and the field
# each is shown by.
REFUSED_FIELDS = {
    "item_category_current": "category_id",
    "item_unit_fixed": "unit",
}


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
        "alias_added": ALIAS_ADDED,
        "item_renamed": RENAMED,
        "alias_removed": ALIAS_REMOVED,
    }
)

# What "Same as the item", "Track" and "Don't track" send for a package.
TRACKING = {"": None, "on": True, "off": False}


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
        },
    )


def _moved(session: Session, item_id: int) -> bool:
    return exists_where(session, Movement.item_id == item_id)


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
    switching = (values or {}).get("unit") == "each" != item.unit
    if not switching or "unit" not in (problems or {}):
        return None
    package = items.fractional_package(session, item.id)
    if package is None or forms.size_has_fraction(package):
        return None
    return package.id


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
) -> HTMLResponse:
    """The item's page; its open packages sorted as `asked`, all of them."""
    here = f"{ITEMS}/{item.id}"
    packages = queries.packages_of_item(session, item.id)
    view = View.read(here, OPEN_SORTING, asked, anchor=OPEN_PACKAGES)
    opened = in_order(
        open_packages.shown(session, OpenPackage.item_id == item.id), view
    )
    log.debug("open packages listed", item_id=item.id, found=len(opened), sort=view.by)
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
            "aliases": items.aliases(session, item.id),
            # Only a current one's size can change.
            "sizes_fixed": items.sizes_fixed(
                session, [each.id for each in packages if each.retired_at is None]
            ),
            "undo": undo,
            "categories": [
                c for c in queries.categories_by_name(session) if c.retired_at is None
            ],
            "units": UNITS,
            "unit_fixed": _moved(session, item.id),
            "in_stock": item_total(session, item.id),
            "labels": labels.current_of(session, Label.item_id == item.id),
            "open_packages": opened,
            "open_view": view,
            "zone": site_zone(session),
            "problems": problems,
            "values": values,
            "short_fraction": _short_fraction(session, item, problems, values),
            "alert": alert,
            **_name_field(session, similar),
        },
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
    refused: Mapping[str, Words] | None = None,
    **query: str,
) -> Response:
    """A change to an item's package or alias, made and committed, then the
    item page saying `notice` by `section`; refused, said above the page,
    while the item is retired, or as `refused` says for its own rules."""
    said = {RETIRED: RULES[RETIRED], **(refused or {})}
    _, broken = refusal(session, change, *said)
    if broken:
        found = session.get_one(Item, item_id)
        return _item_page(request, session, signed, found, alert=said[broken])
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
    fields, problems, typed = forms.item_fields(session, form)
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
    session: DbSession,
    listing: Listed,
    item: Annotated[list[int], Form()] = [],  # noqa: B006  FastAPI copies it.
    track: Annotated[str, Form()] = "",
) -> Response:
    """Set "track each opened package" on the items ticked, on the page
    shown, and come back to it as it was: the list's view comes in the
    form's address."""
    back = listing.link()
    if TRACKING.get(track) is None:
        return not_found()
    if not item:
        return redirect(back, notice="tracking_none")
    changed = items.track_items(session, item, track == "on")
    session.commit()
    if not changed:
        return redirect(back, notice="tracking_same")
    return redirect(back, notice=f"tracking_{track}")


@router.get(f"{ITEMS}/{{item_id}}")
def item(
    request: Request,
    signed: AdminSignedIn,
    session: DbSession,
    item_id: int,
    asked: AskedView,
    undo: str = "",
) -> Response:
    found = session.get(Item, item_id)
    if found is None:
        return not_found()
    # A package just retired, offered back.
    back = undo_retire(session, ItemPackage, undo, f"{ITEMS}/{item_id}/packages")
    return _item_page(request, session, signed, found, undo=back, asked=asked)


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
    fields, problems, typed = forms.item_fields(session, form, found)
    renamed = None
    if fields is not None:
        seen = form_version(form.get("version", ""))
        same = forms.same_name(session, found, form, fields, typed)
        asked = None if same else []
        saving = _deciding(
            session,
            typed,
            signed,
            lambda: items.save(session, found, fields, seen=seen),
        )
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
    if problems:
        return _item_page(
            request, session, signed, found, problems=problems, values=form
        )
    _decided(session, typed, item_id=item_id)
    session.commit()
    if renamed is None:
        return redirect(f"{ITEMS}/{item_id}", notice="saved")
    return redirect(f"{ITEMS}/{item_id}", notice="item_renamed", section="aliases")


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
    parsed, problems = forms.package_fields(found, name, typed.asked, size)
    if parsed is not None and not problems:
        package, broken = refusal(
            session,
            _deciding(
                session,
                typed,
                signed,
                lambda: items.add_package(session, found, name, parsed),
            ),
            "unique",
            RETIRED,
            items.READ_ALIKE,
            items.PACKAGE_WHOLE,
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
        else:
            problems["package_name"] = (
                forms.read_alike(session, typed)
                if broken == items.READ_ALIKE
                else forms.PACKAGE_TAKEN
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
    parsed, problems = forms.package_fields(
        found, name, typed.asked, size, kept=package.size
    )
    if parsed is not None and not problems:
        seen = form_version(package_version)
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
            "package_size_fixed",
            SHORT_UNDER_SIZE,
            items.PACKAGE_WHOLE,
            *ALERTS,
        )
        # Its size field is gone once fixed: said above the page, as for a
        # form that went stale.
        if broken in {*ALERTS, "package_size_fixed"}:
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
    # Beside the fields of the package it was sent for, with the units
    # chosen, if any.
    values = {
        **form,
        "package_id": str(package_id),
        "package_name": name,
        "size": size,
        "package_version": package_version,
    }
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
    return _item_changed(
        request,
        session,
        signed,
        item_id,
        lambda: items.restore_package(session, package),
        "package_restored",
        # Nor with a fraction, to an item counted each.
        refused={items.PACKAGE_WHOLE: lazy(forms.fraction_brought_back, package)},
    )


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
    """A package's own "track each opened package", or as its item says."""
    package = _package(session, item_id, package_id)
    if package is None or track not in TRACKING:
        return not_found()
    return _item_changed(
        request,
        session,
        signed,
        item_id,
        lambda: items.track_package(session, package, TRACKING[track]),
        "package_tracking",
    )


@router.post(f"{ITEMS}/{{item_id}}/packages/{{package_id}}/settings")
def package_settings(
    request: Request,
    signed: AdminSignedIn,
    session: DbSession,
    item_id: int,
    package_id: int,
    short_below: Annotated[str, Form()] = "",
    minimum_sealed: Annotated[str, Form()] = "",
) -> Response:
    """A package's too-short-to-use length and minimum sealed, each blank
    for none."""
    package = _package(session, item_id, package_id)
    if package is None:
        return not_found()
    found = session.get_one(Item, item_id)
    short, minimum, problems = forms.package_settings_fields(
        package, found.unit, short_below, minimum_sealed
    )
    # Beside the fields of the package it was sent for.
    values = {
        "package_id": str(package_id),
        "short_below": short_below,
        "minimum_sealed": minimum_sealed,
    }
    if problems:
        return _item_page(
            request, session, signed, found, problems=problems, values=values
        )
    _, broken = refusal(
        session,
        lambda: items.package_settings(session, package, short, minimum),
        RETIRED,
        SHORT_UNDER_SIZE,
        items.PACKAGE_WHOLE,
    )
    if broken == RETIRED:
        return _item_page(request, session, signed, found, alert=RULES[RETIRED])
    if broken:
        # Resized meanwhile to no more than it, or its item made each: said
        # beside it, as above.
        words = (
            WHOLE_UNITS if broken == items.PACKAGE_WHOLE else forms.SHORTER_THAN_WHOLE
        )
        problems = {"short_below": words}
        return _item_page(
            request, session, signed, found, problems=problems, values=values
        )
    session.commit()
    return redirect(f"{ITEMS}/{item_id}", notice="package_settings", section="packages")


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
