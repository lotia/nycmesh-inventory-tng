"""The server's words, read through Babel's catalogue (phrases.py,
docs/decisions/0022), in Python and in the templates."""

import importlib
import pkgutil
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from zoneinfo import ZoneInfo, available_timezones

import pytest
from babel.messages.catalog import Catalog, Message
from babel.support import LazyProxy, Translations
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

import inventory_tng
from inventory_tng import (
    accounts,
    activity,
    labels,
    mail,
    over_recorded,
    reading_rules,
    units,
)
from inventory_tng.admin import admins as admin_admins
from inventory_tng.admin import common, counts, length_checks, stock, used_up
from inventory_tng.admin import devices as admin_devices
from inventory_tng.admin import items as admin_items
from inventory_tng.admin import over_recorded as admin_over_recorded
from inventory_tng.admin import reading_rules as admin_reading_rules
from inventory_tng.admin import settings as admin_settings
from inventory_tng.admin import volunteers as admin_volunteers
from inventory_tng.models import (
    TRANSACTION_KINDS,
    UNITS,
    AdjustmentReason,
    Admin,
    AdminSession,
    Authenticator,
    Category,
    Device,
    Item,
    ItemAlias,
    ItemPackage,
    Label,
    Location,
    Movement,
    OpenPackage,
    OverRecorded,
    ReadingRule,
    SignInSettings,
    Volunteer,
)
from inventory_tng.pages import retired_holding, templates
from inventory_tng.phrases import (
    BRAND,
    ENGLISH_PO,
    Words,
    compiled,
    gettext,
    lazy_gettext,
    lazy_pgettext,
    ngettext,
    pgettext,
    read,
    speaking,
)
from inventory_tng.resets import RESET_NOTICE

from helpers.devices import new_device
from helpers.rows import add
from helpers.sign_in import new_admin
from helpers.stock import Spools, cut, length, report, retire, write

# Each letter accented, so English left out of the catalogue shows plain.
ACCENTS = str.maketrans(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ",
    "àƀćďēƒĝĥĩĵķĺɱńōƥʠŕśţũʋŵẋŷźÀƁĆĎĒƑĜĤĨĴĶĹḾŃŌƤɊŔŚŢŨṼŴẊŶŹ",
)
# A message's placeholders, and a literal per cent, kept as they are.
PLACEHOLDER = re.compile(r"(%\(\w+\)[sd]|%%)")


def accented(message: str) -> str:
    parts = PLACEHOLDER.split(message)
    return "".join(
        part if index % 2 else part.translate(ACCENTS)
        for index, part in enumerate(parts)
    )


def lone_per_cents(message: str) -> list[str]:
    """Each per cent sign that is neither a placeholder nor `%%`."""
    return re.findall("%", PLACEHOLDER.sub("", message))


def forms(message: Message) -> tuple[str, ...]:
    """A message's English: one form, or a plural's forms."""
    return (message.id,) if isinstance(message.id, str) else tuple(message.id)


def pseudo_locale() -> Translations:
    """The English catalogue with every message accented, built by Babel."""
    catalogue = read(ENGLISH_PO)
    for message in catalogue:
        if not message.id:  # The header.
            continue
        said = tuple(accented(form) for form in forms(message))
        message.string = said[0] if isinstance(message.id, str) else said
    return compiled(catalogue)


PSEUDO = pseudo_locale()
SHELF = SimpleNamespace(id=7, name="Ωμέγα")

# What has moved into the catalogue, each as it is shown. Each part of
# moving the words (inventory-tng-oic) adds what it moved, until every
# page is here.
NAME = SHELF.name


def named_phrases() -> list[LazyProxy]:
    """Every phrase the server's Python names as a constant, alone or in a
    table (a dict, tuple or list, such as NOTICES), in every module."""
    found: list[LazyProxy] = []

    def gather(value: object, depth: int) -> None:
        if isinstance(value, LazyProxy):
            found.append(value)
        elif depth and isinstance(value, dict):
            for each in value.values():
                gather(each, depth - 1)
        elif depth and isinstance(value, (tuple, list)):
            for each in value:
                gather(each, depth - 1)

    for module in pkgutil.walk_packages(inventory_tng.__path__, "inventory_tng."):
        for value in vars(importlib.import_module(module.name)).values():
            gather(value, 3)
    return found


# Each phrase said with values, by its function, the values not English.
SAID_WITH_VALUES: list[Callable[[], Words]] = [
    lambda: accounts.username_problem("!") or "",
    lambda: accounts.name_up_to(9),
    lambda: accounts.password_problem("", "") or "",
    lambda: accounts.password_problem("." * 1000, "") or "",
    lambda: common.up_to(9),
    lambda: common.at_most_places(3),
    lambda: common.revoke_title(NAME),
    *(lambda said=said: said(NAME, NAME) for said in activity.WORDS.values()),
    *(lambda said=said: said(NAME) for said in admin_items.TAKEN_FIELDS.values()),
    *(lambda said=said: said(NAME) for said in admin_items.ALIAS_TAKEN.values()),
    lambda: admin_items.read_as_one(NAME),
    *(lambda said=said: said(1, NAME) for said in reading_rules.NAMED.values()),
    lambda: reading_rules.both(NAME, NAME),
    lambda: reading_rules.near(NAME),
    lambda: admin_reading_rules.reading_title(NAME),
    lambda: admin_volunteers.merge_title(NAME, NAME),
    lambda: admin_admins.link_title(NAME),
    lambda: admin_admins.reset_title(NAME),
    lambda: admin_devices.uses_problem(9),
    lambda: admin_devices.days_problem(9),
    lambda: admin_devices.revoke_many_title(1),
    lambda: admin_devices.revoke_many_title(3),
    *(lambda said=said: said(1, 9) for said in admin_settings.WHOLE.values()),
    lambda: admin_settings.whole_minutes(1, 9),
    lambda: used_up.close_title(NAME),
    lambda: counts.count_title(NAME),
    lambda: counts.moved_meanwhile(0),
    lambda: counts.moved_meanwhile(1),
    lambda: counts.moved_meanwhile(3),
    lambda: counts.sealed_how_many(NAME),
    lambda: counts.opened_left(NAME, "2026-11-15"),
    lambda: stock.recorded_more_view(2),
    lambda: admin_over_recorded.place_brought_back(NAME),
    lambda: admin_over_recorded.not_changed(NAME),
    lambda: admin_over_recorded.waiting_view(2),
    *(
        lambda said=said: said(NAME, NAME)
        for said in admin_over_recorded.RETIRED_EMPTY.values()
    ),
    lambda: over_recorded.times(2, NAME),
    lambda: over_recorded.from_an_open_package(NAME),
    lambda: over_recorded.sealed(NAME),
    lambda: over_recorded.opened(NAME),
    lambda: over_recorded.loose(NAME),
    lambda: over_recorded.held_words(NAME),
    lambda: over_recorded.adjusted_at(NAME),
    lambda: over_recorded.end_only(NAME),
    lambda: over_recorded.closed_on_day(NAME, NAME),
    lambda: over_recorded.counted_on(NAME, NAME),
    lambda: labels.package_label(NAME, NAME, NAME),
    lambda: labels.place_words(NAME, NAME),
    *(lambda said=said: said("") for said in units.NAMES.values()),
]


def all_mail() -> list[mail.Message]:
    """Every mail, each branch of it, with values that are not English."""
    return [
        mail.reset_link(NAME, 1, NAME),
        *(
            build(NAME, 1)
            for build in (
                mail.reset_link_made,
                mail.invited,
                mail.restored,
                mail.recovery_link_made,
                mail.invited_by_operator,
                mail.let_in,
                mail.password_changed,
                mail.email_changed,
                mail.authenticators_retired,
                mail.left_without_a_factor,
                mail.retired,
                mail.recovery_codes_made,
                mail.recovery_code_used(0),
                mail.recovery_code_used(1),
                mail.recovery_code_used(3),
                mail.recovery_code_used(9),
                mail.authenticators_changed(added=True),
                mail.authenticators_changed(added=False),
                mail.activity(NAME),
                *(
                    mail.sign_in_settings_changed(
                        SignInSettings(
                            totp_policy=policy,
                            session_days=days,
                            confirm_hours=days,
                            confirm_idle_minutes=days,
                            account_change_minutes=change,
                        )
                    )
                    for policy, days, change in (
                        ("required", 1, 0),
                        ("optional", 2, 1),
                        ("required", 3, 5),
                    )
                ),
            )
        ),
    ]


def every_mail() -> str:
    """Every mail's subject and body, without the app's name, which is the
    same in every language."""
    return " ".join(f"{m.subject} {m.body}" for m in all_mail()).replace(BRAND, "")


def test_every_mail_names_the_app_in_its_subject_and_body() -> None:
    for told in all_mail():
        assert BRAND in told.subject, told.kind
        assert BRAND in told.body, told.kind


def render(name: str, **context: object) -> str:
    """Template `name`, rendered with `context`."""
    return templates.get_template(name).render(context)


def from_macros(source: str, **context: object) -> str:
    """`source`, a template calling shared macros, rendered with `context`."""
    return templates.env.from_string(source).render(context)


def without_brand(html: str) -> str:
    """`html`'s words but the app's name, the same in every language, which
    the masthead shows in parts."""
    words = re.sub(r"<script.*?</script>|<[^>]*>", " ", html, flags=re.DOTALL)
    return " ".join(words.split()).replace(BRAND, "")


def link(*_: object, **__: object) -> str:
    return "/here"


# What a page signed in shows around its own words (admin_base.html).
SIGNED_IN = {
    "request": SimpleNamespace(
        url=SimpleNamespace(path="/admin/stock/x"),
        app=SimpleNamespace(state=SimpleNamespace(bundle=SimpleNamespace(tags=link))),
    ),
    "admin": SimpleNamespace(username=NAME),
    "sections": [("/admin/stock", NAME, SimpleNamespace(count=2, words=NAME))],
    "title": NAME,
    "back_link": ("/admin", NAME),
}
VIEW = SimpleNamespace(
    aria_sort=lambda column: "descending",
    sort_link=link,
    sizes=[25, None],
    size_link=link,
    per_page=25,
    searching=True,
    best_match_link=link,
    by=None,
    here="/here",
    unfiltered_fields=lambda: [("q", NAME)],
    filters=True,
    unfiltered_link=link,
    fields=lambda *_: [("q", NAME)],
    filtered=lambda name: "",
    pages=3,
    page=2,
    link=link,
)


def listing(**differs: object) -> SimpleNamespace:
    """A long list with search (admin/listing.py), as _listing.html reads it."""
    return SimpleNamespace(
        **{
            "here": "/here",
            "view": VIEW,
            "search": NAME,
            "suggest_address": "/here/suggest",
            "lapsed": "retired",
            "link": link,
            "show": "retired",
            "narrowed": True,
            "picked": None,
            "found": 1,
            "total": 3,
            "rows": [],
        }
        | differs
    )


LISTING_PARTS = """{% from "_listing.html" import sort_heading, sizes, filter_form,
  search_box, filters, summary, empty, pager %}
{{ sort_heading(view, "name", "") }}{{ sizes(view) }}
{% call filter_form(view) %}{% endcall %}
{{ search_box(listing, "") }}{{ filters(listing) }}
{% call(total, lapsed) summary(listing) %}{{ total }}{% endcall %}
{{ empty(listing, 2, "") }}{{ pager(view) }}"""
FORMS = SimpleNamespace(
    sealed=[(NAME, Decimal(2), Decimal(3))],
    open=[Decimal(1)],
    loose=Decimal(2),
    short_ends=[Decimal(1)],
    usable=Decimal(4),
)
# A zone whose name has no letters, as a time shows it.
ZONE = ZoneInfo("Etc/GMT-3")
RETIRED_PARTS = """{% from "_retired_record.html" import retired_record, retire_section
  with context %}{% from "_retired.html" import retired %}
{% for person in [true, false] %}
{% call retired_record(row, person=person) %}{% endcall %}
{% call retire_section("", person=person) %}{% endcall %}
{% endfor %}{{ retired(row) }}"""


def retired_parts() -> str:
    row = SimpleNamespace(retired_at=datetime(2026, 11, 15, tzinfo=UTC))
    return " ".join(
        from_macros(RETIRED_PARTS, row=row, here="/here", zone=ZONE, retired_by=by)
        for by in (NAME, None)
    )


READING = SimpleNamespace(called=NAME, kind_words=NAME)


def unit_read(**differs: object) -> SimpleNamespace:
    """A unit in a name, as units.found_in reads it (_units_read.html)."""
    return SimpleNamespace(
        **{
            "key": NAME,
            "typed": NAME,
            "asks": False,
            "offered": [READING, SimpleNamespace(called=NAME, kind_words=NAME)],
            "shown": lambda reading: NAME,
            "read": READING,
            "clue": [NAME, NAME],
            "rule": SimpleNamespace(respell=True),
        }
        | differs
    )


def units_read() -> str:
    """Each way _units_read.html says a unit was read, or asks."""
    each = [
        unit_read(asks=True),
        unit_read(),
        unit_read(clue=[], rule=None),
        unit_read(read=None),
    ]
    return render(
        "_units_read.html",
        units_read=lambda typed: each,
        shown_as=lambda typed, sent: NAME,
        typed=NAME,
        sent={},
        not_a_unit="",
    )


DAY = datetime(2026, 11, 15, tzinfo=UTC)
PLACE = SimpleNamespace(id=1, name=NAME, retired_at=DAY)


def recorded(**differs: object) -> SimpleNamespace:
    """A line recorded with more than was moved, as over_recorded.html
    shows it (over_recorded.Shown)."""
    return SimpleNamespace(
        **{
            "row": SimpleNamespace(id=1),
            "item": NAME,
            "item_id": 1,
            "amount": NAME,
            "from_place": NAME,
            "to_place": NAME,
            "transaction_id": 1,
            "recorded_at": DAY,
            "moved_whole": None,
            "one_counted": None,
            "both_right": None,
        }
        | differs
    )


def recorded_more() -> str:
    """over_recorded.html, a row of each kind, and a refusal."""
    one = {
        "counted": NAME,
        "on": DAY,
        "still": NAME,
        "correct": NAME,
        "place_id": 1,
        "place": NAME,
        "countable": True,
    }
    both = {"left": NAME, "left_on": DAY, "arrived": NAME, "arrived_on": DAY}
    waiting = [
        recorded(moved_whole=SimpleNamespace(count="1", words=NAME)),
        *(
            recorded(one_counted=SimpleNamespace(**one, how=how))
            for how in ("counted", "closed")
        ),
        *(
            recorded(both_right=SimpleNamespace(**both, arrived_how=how))
            for how in ("counted", "closed")
        ),
        recorded(from_place=None),
        recorded(to_place=None),
    ]
    refused = SimpleNamespace(shown=waiting[1], words=NAME, place=PLACE, at=None)
    shown = without_brand(
        render(
            "over_recorded.html",
            **SIGNED_IN,
            view=VIEW,
            recorded_views=[],
            refused=refused,
            values={},
            problems={},
            reasons={"a": NAME},
            waiting=waiting,
            seen=link,
            only=SimpleNamespace(name_shown=NAME),
            zone=ZONE,
        )
    )
    # A day's month, as strftime names it until the dates are said in the
    # reader's language (inventory-tng-ist.2).
    return re.sub(rf"\b{DAY:%b}\b", "", shown)


def count_page() -> str:
    """count.html for a retired place, a recount of an item counted by
    its forms and of one in loose units, and the fields for more."""
    item = SimpleNamespace(id=1, name_shown=NAME, unit=NAME)
    package = SimpleNamespace(id=1, name_shown=NAME)
    return without_brand(
        render(
            "count.html",
            **SIGNED_IN,
            place=PLACE,
            shown=SimpleNamespace(only=item, search=NAME, query=link),
            items=[1],
            rows=[
                (item, Decimal(1)),
                (SimpleNamespace(**(vars(item) | {"id": 2})), Decimal(-1)),
            ],
            forms={
                1: [
                    SimpleNamespace(
                        field="open_1", words=NAME, amount=Decimal(1), open_id=1
                    )
                ]
            },
            recount={1: (Decimal(1),) * 3, 2: (Decimal(1),) * 3},
            values={},
            problems={},
            others=[item],
            found_packages=[(package, NAME, NAME)],
        )
    )


def counts_page() -> str:
    """counts.html, a count and an adjustment among those listed."""
    entry = SimpleNamespace(
        transaction=SimpleNamespace(kind="count", created_at=DAY, time_zone=ZONE),
        lines=[],
        found=True,
        place=NAME,
        admin=NAME,
        reason=NAME,
    )
    return without_brand(
        render(
            "counts.html",
            **SIGNED_IN,
            view=VIEW,
            places=[PLACE],
            values={},
            problems={},
            items=[],
            reasons=[],
            counted_places=[],
            kinds=["count", "adjustment"],
            counters=[],
            recent=[entry],
        )
    )


COVERED: dict[str, Callable[[], str]] = {
    "every phrase named as a constant": lambda: " ".join(
        str(phrase) for phrase in named_phrases()
    ),
    "every phrase said with values": lambda: " ".join(
        str(said()) for said in SAID_WITH_VALUES
    ),
    "the reset notice": lambda: str(RESET_NOTICE),
    "every mail": every_mail,
    "one retired place holding stock": lambda: retired_holding(1),
    "retired places holding stock": lambda: retired_holding(3),
    "_retired_holding.html": lambda: templates.get_template(
        "_retired_holding.html"
    ).render(retired_places=[SHELF, SHELF], counts_path="/admin/counts"),
    "page.html": lambda: without_brand(
        render(
            "page.html",
            **SIGNED_IN,
            tags=SimpleNamespace(styles=[], preloads=[], scripts=[]),
        )
    ),
    "error.html, admin_base.html and _masthead.html": lambda: without_brand(
        render("error.html", **SIGNED_IN, words=NAME)
    ),
    "_listing.html, narrowed by a search": lambda: from_macros(
        LISTING_PARTS, view=VIEW, listing=listing()
    ),
    "_listing.html, narrowed by a pick": lambda: from_macros(
        LISTING_PARTS, view=VIEW, listing=listing(picked=NAME, lapsed="inactive")
    ),
    "_listing.html, not narrowed": lambda: from_macros(
        LISTING_PARTS, view=VIEW, listing=listing(narrowed=False, search="")
    ),
    "_listing.html, filtered": lambda: from_macros(
        LISTING_PARTS,
        view=VIEW,
        listing=listing(narrowed=False, search="", show="current"),
    ),
    "_forms.html": lambda: from_macros(
        '{% from "_forms.html" import forms_lines %}{{ forms_lines(f, unit) }}',
        f=FORMS,
        unit=NAME,
    ),
    "_retired_record.html and _retired.html": retired_parts,
    "_notice.html": lambda: render(
        "_notice.html", notice=NAME, notice_section=None, section_id=None, undo="/"
    ),
    "_shown_once.html": lambda: render("_shown_once.html"),
    "_code_field.html": lambda: " ".join(
        render("_code_field.html", code_totp=totp, totp_only=only, limits={})
        for totp, only in ((True, True), (True, False), (False, False))
    ),
    "_totp_fields.html": lambda: render(
        "_totp_fields.html", values={}, problems={}, limits={}, totp_optional=True
    ),
    "_units_read.html": units_read,
    "_item_name.html, with similar names": lambda: render(
        "_item_name.html",
        values={},
        problems={},
        default=NAME,
        again=NAME,
        units_read=lambda typed: [],
        shown_as=lambda typed, sent: "",
        similar=[
            SimpleNamespace(
                item=SimpleNamespace(id=1, name_shown=NAME, retired_at=1), alias=NAME
            ),
            SimpleNamespace(
                item=SimpleNamespace(id=2, name_shown=NAME, retired_at=None), alias=None
            ),
        ],
    ),
    "over_recorded.html, a row of each kind": recorded_more,
    "count.html, a recount": count_page,
    "counts.html, a count listed": counts_page,
    "code_made.html": lambda: without_brand(
        render(
            "code_made.html",
            **SIGNED_IN,
            code=SimpleNamespace(max_uses=2, expires_at=DAY, label=NAME),
            qr="",
            phrase=NAME,
            link="/",
            register=NAME,
            zone=ZONE,
        )
    ),
    "device_revoke.html": lambda: without_brand(
        render(
            "device_revoke.html",
            **SIGNED_IN,
            devices=[SimpleNamespace(id=1, name=NAME)],
            filter={},
            keep="/",
            here="/",
        )
    ),
    "the sign-in pages' forms": lambda: " ".join(
        without_brand(render(name, **SIGNED_IN, **context))
        for name, context in (
            (
                "recover.html",
                {"values": SimpleNamespace(username=NAME), "problems": {}},
            ),
            ("setup.html", {"off": True}),
            ("setup.html", {"values": {}, "problems": {}, "totp_optional": True}),
            ("recovery_codes.html", {"codes": [1]}),
            (
                "authenticator_new.html",
                {"required": True, "values": {}, "problems": {}},
            ),
            *(
                (
                    "reset.html",
                    {
                        "link": SimpleNamespace(resets_authenticators=True, kind=kind),
                        "totp_required": True,
                        "problems": {},
                    },
                )
                for kind in ("reset", "recovery", "emailed")
            ),
            *(
                ("confirm.html", {"uses_totp": totp, "retype": ["password_again"]})
                for totp in (True, False)
            ),
            *(
                (
                    "link_made.html",
                    {
                        "brought_back": True,
                        "kind": kind,
                        "other": SimpleNamespace(username=NAME),
                    },
                )
                for kind in ("reset", "invite")
            ),
            ("sent_again.html", {"made": "change", "back": "/"}),
        )
    ),
    "_print_controls.html": lambda: from_macros(
        '{% from "_print_controls.html" import print_controls %}'
        '{% call print_controls("") %}{% endcall %}'
    ),
}


@pytest.mark.parametrize("part", COVERED)
def test_what_has_moved_says_nothing_in_plain_english(part: str) -> None:
    with speaking(PSEUDO):
        shown = COVERED[part]()
    # What people read: neither scripts, tags nor character references.
    words = re.sub(r"<script.*?</script>|<[^>]*>|&\w+;", " ", shown, flags=re.DOTALL)
    assert re.findall(r"[A-Za-z]{2,}", words) == []
    assert words.strip()


@dataclass
class World:
    """What the pages walked below show: spools of cable in a store, one
    opened, with a label and an alias; an area in the store; a volunteer
    holding stock; a retired item, category and place; and stock to put
    right, as test_query_counts.everything makes it: an item to reorder,
    less than none at a place, open packages cut below zero, said to show
    the wrong length and said used up, and a line recorded with more than
    was moved, and another one corrected."""

    spools: Spools
    opened: OpenPackage
    area: Location
    custody: Location
    retired_item: Item
    retired_category: Category
    retired_place: Location
    label: Label
    other: Volunteer
    merged: Volunteer
    device: Device
    rule: ReadingRule
    other_admin: Admin
    retired_admin: Admin


def world(session: Session) -> World:
    spools = Spools(session)
    opened = spools.opened()
    cable = spools.cable
    # An alias is never changed: named not in English as it is made.
    add(session, ItemAlias(item_id=cable.id, name=f"{NAME} ἄλλο"))
    label = add(session, Label(item_id=cable.id, package_id=spools.spool.id))
    area = add(session, Location(name="Shelf", kind="area", parent_id=spools.store.id))
    custody = add(
        session,
        Location(
            name="Custody",
            kind="custody",
            volunteer_id=spools.place.volunteer.id,
        ),
    )
    retired_category = add(session, Category(name="Old"))
    retired_item = add(session, Item(name="Old", category_id=retired_category.id))
    retired_place = add(session, Location(name="Old", kind="store"))
    retire(session, retired_item, retired_category, retired_place)
    stock_to_put_right(spools)
    other = add(session, Volunteer(name="Other"))
    merged = add(
        session,
        Volunteer(name="Merged", merged_into_id=other.id, retired_at=datetime.now(UTC)),
    )
    device, _ = new_device(session)
    revoked, _ = new_device(session)
    revoked.revoked_at = datetime.now(UTC)
    rule = add(session, ReadingRule(token="P", decided_by_admin_id=admin_id(session)))
    # A unit after a number no rule settles, for the readings to propose.
    add(session, Item(name=f"{NAME} 10g", category_id=cable.category_id))
    other_admin, _ = new_admin(session, "grace", email="grace@example.org")
    retired_admin, _ = new_admin(session, "hopper", totp=False)
    retired_admin.retired_at = datetime.now(UTC)
    return World(
        spools,
        opened,
        area,
        custody,
        retired_item,
        retired_category,
        retired_place,
        label,
        other,
        merged,
        device,
        rule,
        other_admin,
        retired_admin,
    )


def admin_id(session: Session) -> int:
    """The administrator signed in (admin_client)."""
    found = session.scalars(select(Admin.id)).first()
    assert found is not None
    return found


def stock_to_put_right(spools: Spools) -> None:
    session = spools.session
    spools.cable.minimum = Decimal(10_000)
    spools.spool.minimum_sealed = 99
    write(session, spools.place, lines=[cut(spools.opened(), "400")])
    write(session, spools.place, **report(spools, length(spools.opened(), "250")))
    # Radios the store never had moved out: less than none there.
    write(session, spools.place)
    used_up = report(spools) | {"lengths": None, "used_up": [str(spools.opened().key)]}
    write(session, spools.place, **used_up)
    for corrected in (False, True):
        transaction = write(session, spools.place)
        movement_id = session.scalars(
            select(Movement.id).where(Movement.transaction_id == transaction.id)
        ).one()
        over_recorded.raise_checks(session, {movement_id: Decimal(1)})
        if corrected:
            check = session.scalars(
                select(OverRecorded).where(OverRecorded.movement_id == movement_id)
            ).one()
            over_recorded.correct(
                session, check.id, over_recorded.seen(check), admin_id(session)
            )


def not_english(session: Session) -> None:
    """Every name the pages show, made not English, so that what is left in
    English on a page is a word not said through the catalogue."""
    # Adjustment reasons too, though the seeded ones stay English
    # (inventory-tng-0z5): they are rows, as names are.
    for model in (Item, ItemPackage, Category, Location, Volunteer, AdjustmentReason):
        for row in session.scalars(select(model)):
            if re.search("[A-Za-z]{2,}", row.name):
                row.name = f"{NAME} {row.id}"
    for authenticator in session.scalars(select(Authenticator)):
        authenticator.name = NAME
    for signed_in in session.scalars(select(AdminSession)):
        signed_in.user_agent = NAME
    for admin in session.scalars(select(Admin)):
        # ASCII, as a username is: one letter, so no word.
        admin.username = f"a{admin.id:04d}"
    session.flush()


def data_words(session: Session) -> set[str]:
    """What a page shows that is the same in every language: unit keys
    (models.UNITS), time zones and their abbreviations, and labels' codes.
    A unit's symbol too (is_a_symbol)."""
    now = datetime.now(UTC)
    zones = available_timezones()
    shortened = {now.astimezone(ZoneInfo(zone)).tzname() or "" for zone in zones}
    codes = set(session.scalars(select(Label.code)))
    return {*UNITS, *zones, *shortened, *codes}


def is_a_symbol(word: str) -> bool:
    """Whether `word` is a unit's symbol as a name shows it, such as Gbps."""
    reading = units.read(word)
    return reading is not None and reading.shown == word


# Every page whose words have all moved, by a path in a World.
PAGES: dict[str, Callable[[World], str]] = {
    "items": lambda w: "/admin/items",
    "items, retired": lambda w: "/admin/items?show=retired",
    "items, searched": lambda w: f"/admin/items?q={NAME}",
    "an item": lambda w: f"/admin/items/{w.spools.cable.id}",
    "a retired item": lambda w: f"/admin/items/{w.retired_item.id}",
    "an open package to close": lambda w: (
        f"/admin/items/{w.spools.cable.id}/open-packages/{w.opened.id}/close"
    ),
    "categories": lambda w: "/admin/categories",
    "a category": lambda w: f"/admin/categories/{w.spools.cable.category_id}",
    "a retired category": lambda w: f"/admin/categories/{w.retired_category.id}",
    "locations": lambda w: "/admin/locations",
    "a store": lambda w: f"/admin/locations/{w.spools.store.id}",
    "an area": lambda w: f"/admin/locations/{w.area.id}",
    "a volunteer's custody": lambda w: f"/admin/locations/{w.custody.id}",
    "a retired place": lambda w: f"/admin/locations/{w.retired_place.id}",
    "stock by place": lambda w: "/admin/stock",
    "stock by item": lambda w: "/admin/stock?by=item",
    "stock, filtered to nothing": lambda w: "/admin/stock?q=qqqq",
    "reorder": lambda w: "/admin/stock/reorder",
    "needs counting": lambda w: "/admin/stock/needs-counting",
    "used up": lambda w: "/admin/stock/used-up",
    "length checks": lambda w: "/admin/stock/length-checks",
    **{
        f"length checks, {kind.path}": lambda w, kind=kind: kind.path
        for kind in length_checks.KINDS
    },
    "recorded more than moved": lambda w: "/admin/stock/recorded-more",
    "recorded more than moved, settled": lambda w: "/admin/stock/recorded-more/settled",
    "counts": lambda w: "/admin/counts",
    "a count": lambda w: f"/admin/counts/{w.spools.store.id}",
    "a count of one item": lambda w: (
        f"/admin/counts/{w.spools.store.id}?item={w.spools.cable.id}&q={NAME}"
    ),
    "labels": lambda w: "/admin/labels",
    "labels, inactive": lambda w: "/admin/labels?show=inactive",
    "labels to print": lambda w: f"/admin/labels/print?label={w.label.id}",
    "a label to revoke": lambda w: f"/admin/labels/{w.label.id}/revoke",
    "volunteers": lambda w: "/admin/volunteers",
    "volunteers, searched": lambda w: f"/admin/volunteers?q={NAME}",
    "a volunteer": lambda w: f"/admin/volunteers/{w.spools.place.volunteer.id}",
    "a volunteer merged": lambda w: f"/admin/volunteers/{w.merged.id}",
    "a volunteer to merge": lambda w: (
        f"/admin/volunteers/{w.spools.place.volunteer.id}/merge?into_id={w.other.id}"
    ),
    "devices": lambda w: "/admin/devices",
    "devices, all": lambda w: "/admin/devices/all?state=all",
    "a device": lambda w: f"/admin/devices/{w.device.id}",
    "a device to revoke": lambda w: f"/admin/devices/{w.device.id}/revoke",
    "a code": lambda w: f"/admin/devices/codes/{w.device.registration_code_id}",
    "a code to revoke": lambda w: (
        f"/admin/devices/codes/{w.device.registration_code_id}/revoke"
    ),
    "unit readings": lambda w: "/admin/unit-readings",
    "a unit reading": lambda w: f"/admin/unit-readings/{w.rule.id}",
    "unit readings to settle": lambda w: "/admin/unit-readings/seed",
    "unit readings to settle, one by one": lambda w: "/admin/unit-readings/seed?one=1",
    "settings": lambda w: "/admin/settings",
    "home": lambda w: "/admin",
    "administrators": lambda w: "/admin/admins",
    "an administrator": lambda w: f"/admin/admins/{w.other_admin.id}",
    "a retired administrator": lambda w: f"/admin/admins/{w.retired_admin.id}",
    "an administrator to reset": lambda w: f"/admin/admins/{w.other_admin.id}/reset",
    "your account": lambda w: "/admin/account",
    "a new authenticator": lambda w: "/admin/authenticators/new",
}
# Every page one signed out sees, whose words have all moved.
SIGNED_OUT = ["/admin/sign-in", "/admin/forgot", "/admin/reset?token=none"]


@pytest.mark.parametrize("page", PAGES)
def test_a_page_whose_words_have_moved_says_none_in_plain_english(
    page: str, admin_client: TestClient, session: Session
) -> None:
    where = PAGES[page](world(session))
    not_english(session)
    with speaking(PSEUDO):
        response = admin_client.get(where)
    assert response.status_code == 200, where
    words = re.findall(r"[\w/+-]+", re.sub(r"&\w+;", " ", without_brand(response.text)))
    english = {
        word
        for word in words
        if re.search("[A-Za-z]{2,}", word)
        and not is_a_symbol(word)
        # A new authenticator's key, in groups of four to type.
        and not re.fullmatch("[A-Z2-7]{4}", word)
    }
    assert english - data_words(session) == set()


@pytest.mark.parametrize("where", SIGNED_OUT)
def test_a_page_signed_out_says_none_in_plain_english(
    where: str, client: TestClient
) -> None:
    with speaking(PSEUDO):
        response = client.get(where)
    # A reset link that doesn't work says so, as not found.
    assert response.status_code in {200, 404}, where
    words = re.findall(r"[\w/+-]+", re.sub(r"&\w+;", " ", without_brand(response.text)))
    assert [word for word in words if re.search("[A-Za-z]{2,}", word)] == []


def test_english_is_the_catalogues_own_words() -> None:
    assert str(RESET_NOTICE) == "Your account is reset: sign in."
    assert retired_holding(1) == "1 retired place holds stock"
    assert retired_holding(3) == "3 retired places hold stock"


def test_a_lazy_phrase_is_said_in_the_locale_active_when_shown() -> None:
    with speaking(PSEUDO):
        assert str(RESET_NOTICE) == accented("Your account is reset: sign in.")
    assert str(RESET_NOTICE) == "Your account is reset: sign in."


def test_values_fill_their_placeholders_and_a_double_per_cent_is_one() -> None:
    assert gettext("%(n)s of %(m)s", n=1, m=2) == "1 of 2"
    assert gettext("Half: 50%%") == "Half: 50%"
    assert ngettext("%(num)d box", "%(num)d boxes", 2) == "2 boxes"
    assert ngettext("%(num)d %(what)s", "%(num)d %(what)ss", 1, what="box") == "1 box"
    assert str(lazy_gettext("Half: 50%%")) == "Half: 50%"


def test_a_context_tells_two_meanings_of_the_same_english_apart() -> None:
    catalogue = Catalog()
    catalogue.add("To", "Into", context="place")
    catalogue.add("To", "Recipient", context="mail")
    with speaking(compiled(catalogue)):
        assert pgettext("place", "To") == "Into"
        assert str(lazy_pgettext("mail", "To")) == "Recipient"
    assert pgettext("place", "To") == "To"


def test_a_templates_values_are_escaped_and_its_lines_read_as_one() -> None:
    said = templates.env.from_string(
        "{% trans name=name %}Start\n  counting {{ name }}{% endtrans %}"
    ).render(name="<b>")
    assert said == "Start counting &lt;b&gt;"


def test_speaking_ends_with_its_block_even_on_an_error() -> None:
    with pytest.raises(ValueError, match="stop"), speaking(PSEUDO):
        raise ValueError("stop")
    assert str(RESET_NOTICE) == "Your account is reset: sign in."


def test_a_lone_per_cent_is_found() -> None:
    assert lone_per_cents("Half: 50%") == ["%"]
    assert lone_per_cents("%(num)d of 50%%") == []


def test_no_message_has_a_lone_per_cent() -> None:
    # Every message is %-formatted when said, so a lone % would fail the page.
    catalogue = read(ENGLISH_PO)
    said = [form for message in catalogue if message.id for form in forms(message)]
    assert said
    assert [form for form in said if lone_per_cents(form)] == []


def test_every_kind_of_save_has_words_for_a_cut() -> None:
    assert length_checks.CUT_KINDS.keys() == set(TRANSACTION_KINDS)
