"""The server's words, read through Babel's catalogue (phrases.py,
docs/decisions/0022), in Python and in the templates."""

import importlib
import pkgutil
import re
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from decimal import Decimal
from io import StringIO
from types import SimpleNamespace
from typing import get_args
from zoneinfo import ZoneInfo, available_timezones

import pytest
from babel.messages.catalog import Catalog, Message
from babel.messages.pofile import read_po
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
    sign_in,
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
from inventory_tng.admin.listing import Lapsed
from inventory_tng.models import (
    TRANSACTION_KINDS,
    UNITS,
    AdjustmentReason,
    Admin,
    AdminSession,
    Authenticator,
    Category,
    Item,
    ItemPackage,
    Label,
    Location,
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
    number,
    pgettext,
    read,
    speaking,
)
from inventory_tng.resets import RESET_NOTICE

from helpers import markup
from helpers.pages import NOT_ENGLISH, PAGES, Built, built, path

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
SHELF = SimpleNamespace(id=7, name=NOT_ENGLISH)


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
    lambda: common.revoke_title(NOT_ENGLISH),
    *(
        lambda said=said: said(NOT_ENGLISH, NOT_ENGLISH)
        for said in activity.WORDS.values()
    ),
    *(
        lambda said=said: said(NOT_ENGLISH)
        for said in admin_items.TAKEN_FIELDS.values()
    ),
    *(lambda said=said: said(NOT_ENGLISH) for said in admin_items.ALIAS_TAKEN.values()),
    lambda: admin_items.read_as_one(NOT_ENGLISH),
    *(lambda said=said: said(1, NOT_ENGLISH) for said in reading_rules.NAMED.values()),
    lambda: reading_rules.both(NOT_ENGLISH, NOT_ENGLISH),
    lambda: reading_rules.near(NOT_ENGLISH),
    lambda: admin_reading_rules.reading_title(NOT_ENGLISH),
    lambda: admin_volunteers.merge_title(NOT_ENGLISH, NOT_ENGLISH),
    lambda: admin_admins.link_title(NOT_ENGLISH),
    lambda: admin_admins.reset_title(NOT_ENGLISH),
    lambda: admin_devices.uses_problem(9),
    lambda: admin_devices.days_problem(9),
    lambda: admin_devices.revoke_many_title(1),
    lambda: admin_devices.revoke_many_title(3),
    *(lambda said=said: said(1, 9) for said in admin_settings.WHOLE.values()),
    lambda: admin_settings.whole_minutes(1, 9),
    lambda: used_up.close_title(NOT_ENGLISH),
    lambda: counts.count_title(NOT_ENGLISH),
    lambda: counts.moved_meanwhile(0),
    lambda: counts.moved_meanwhile(1),
    lambda: counts.moved_meanwhile(3),
    lambda: counts.sealed_how_many(NOT_ENGLISH),
    lambda: counts.opened_left(NOT_ENGLISH, "2026-11-15"),
    lambda: stock.recorded_more_view(2),
    lambda: admin_over_recorded.place_brought_back(NOT_ENGLISH),
    lambda: admin_over_recorded.not_changed(NOT_ENGLISH),
    lambda: admin_over_recorded.waiting_view(2),
    *(
        lambda said=said: said(NOT_ENGLISH, NOT_ENGLISH)
        for said in admin_over_recorded.RETIRED_EMPTY.values()
    ),
    lambda: over_recorded.times(2, NOT_ENGLISH),
    lambda: over_recorded.from_an_open_package(NOT_ENGLISH),
    lambda: over_recorded.sealed(NOT_ENGLISH),
    lambda: over_recorded.opened(NOT_ENGLISH),
    lambda: over_recorded.loose(NOT_ENGLISH),
    lambda: over_recorded.held_words(NOT_ENGLISH),
    lambda: over_recorded.adjusted_at(NOT_ENGLISH),
    lambda: over_recorded.end_only(NOT_ENGLISH),
    lambda: over_recorded.closed_on_day(NOT_ENGLISH, NOT_ENGLISH),
    lambda: over_recorded.counted_on(NOT_ENGLISH, NOT_ENGLISH),
    lambda: labels.package_label(NOT_ENGLISH, NOT_ENGLISH, NOT_ENGLISH),
    lambda: labels.place_words(NOT_ENGLISH, NOT_ENGLISH),
    *(lambda said=said: said("") for said in units.NAMES.values()),
]


def all_mail() -> list[mail.Message]:
    """Every mail, each branch of it, with values that are not English."""
    return [
        mail.reset_link(NOT_ENGLISH, 1, NOT_ENGLISH),
        *(
            build(NOT_ENGLISH, 1)
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
                mail.activity(NOT_ENGLISH),
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
    """Every mail's subject and body."""
    return " ".join(f"{m.subject} {m.body}" for m in all_mail())


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


# Attributes whose words people read or hear.
SAID_IN = re.compile(
    r'\b(?:title|alt|placeholder|aria-label|aria-description)="([^"]*)"'
)


def words_shown(html: str) -> str:
    """`html`'s words people read or hear: its text and what its tags say
    in attributes, but not its scripts."""
    html = re.sub(r"<script.*?</script>", " ", html, flags=re.DOTALL)
    words = re.sub(
        r"<[^>]*>", lambda tag: " ".join(["", *SAID_IN.findall(tag[0]), ""]), html
    )
    return " ".join(words.split())


def english(html: str) -> list[str]:
    """The words `html` shows in plain English: those with two letters of
    the English alphabet in a row, not counting character references or
    the app's name, the same in every language."""
    shown = words_shown(html).replace(BRAND, "")
    words = re.findall(r"[\w/+-]+", re.sub(r"&\w+;", " ", shown))
    return [word for word in words if re.search("[A-Za-z]{2,}", word)]


def link(*_: object, **__: object) -> str:
    return "/here"


# What a page signed in shows around its own words (admin_base.html).
SIGNED_IN = {
    "request": SimpleNamespace(
        url=SimpleNamespace(path="/admin/stock/x"),
        scope={},
        app=SimpleNamespace(state=SimpleNamespace(bundle=SimpleNamespace(tags=link))),
    ),
    "admin": SimpleNamespace(username=NOT_ENGLISH),
    "sections": [
        ("/admin/stock", NOT_ENGLISH, SimpleNamespace(count=2, words=NOT_ENGLISH))
    ],
    "title": NOT_ENGLISH,
    "back_link": ("/admin", NOT_ENGLISH),
}


def signed_in(name: str, **context: object) -> str:
    """Template `name`, rendered signed in with `context`."""
    return render(name, **SIGNED_IN, **context)


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
    unfiltered_fields=lambda: [("q", NOT_ENGLISH)],
    filters=True,
    unfiltered_link=link,
    fields=lambda *_: [("q", NOT_ENGLISH)],
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
            "search": NOT_ENGLISH,
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
    sealed=[(NOT_ENGLISH, Decimal(2), Decimal(3))],
    open=[Decimal(1)],
    loose=Decimal(2),
    short_ends=[Decimal(1)],
    usable=Decimal(4),
)
# A zone whose name has no letters, as a time shows it.
ZONE = ZoneInfo("Etc/GMT-3")
DAY = datetime(2026, 11, 15, tzinfo=UTC)
PLACE = SimpleNamespace(id=1, name=NOT_ENGLISH, retired_at=DAY)
RETIRED_PARTS = """{% from "_retired_record.html" import retired_record, retire_section
  with context %}{% from "_retired.html" import retired %}
{% for person in [true, false] %}
{% call retired_record(row, person=person) %}{% endcall %}
{% call retire_section("", person=person) %}{% endcall %}
{% endfor %}{{ retired(row) }}"""


def retired_parts() -> str:
    row = SimpleNamespace(retired_at=DAY)
    return " ".join(
        from_macros(RETIRED_PARTS, row=row, here="/here", zone=ZONE, retired_by=by)
        for by in (NOT_ENGLISH, None)
    )


READING = SimpleNamespace(called=NOT_ENGLISH, kind_words=NOT_ENGLISH)


def unit_read(**differs: object) -> SimpleNamespace:
    """A unit in a name, as units.found_in reads it (_units_read.html)."""
    return SimpleNamespace(
        **{
            "key": NOT_ENGLISH,
            "typed": NOT_ENGLISH,
            "asks": False,
            "offered": [
                READING,
                SimpleNamespace(called=NOT_ENGLISH, kind_words=NOT_ENGLISH),
            ],
            "shown": lambda reading: NOT_ENGLISH,
            "read": READING,
            "clue": [NOT_ENGLISH, NOT_ENGLISH],
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
        shown_as=lambda typed, sent: NOT_ENGLISH,
        typed=NOT_ENGLISH,
        sent={},
        not_a_unit="",
    )


def recorded(**differs: object) -> SimpleNamespace:
    """A line recorded with more than was moved, as over_recorded.html
    shows it (over_recorded.Shown)."""
    return SimpleNamespace(
        **{
            "row": SimpleNamespace(id=1),
            "item": NOT_ENGLISH,
            "item_id": 1,
            "amount": NOT_ENGLISH,
            "from_place": NOT_ENGLISH,
            "to_place": NOT_ENGLISH,
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
        "counted": NOT_ENGLISH,
        "on": DAY,
        "still": NOT_ENGLISH,
        "correct": NOT_ENGLISH,
        "place_id": 1,
        "place": NOT_ENGLISH,
        "countable": True,
    }
    both = {
        "left": NOT_ENGLISH,
        "left_on": DAY,
        "arrived": NOT_ENGLISH,
        "arrived_on": DAY,
    }
    waiting = [
        recorded(moved_whole=SimpleNamespace(count="1", words=NOT_ENGLISH)),
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
    refused = SimpleNamespace(shown=waiting[1], words=NOT_ENGLISH, place=PLACE, at=None)
    shown = signed_in(
        "over_recorded.html",
        view=VIEW,
        recorded_views=[],
        refused=refused,
        values={},
        problems={},
        reasons={"a": NOT_ENGLISH},
        waiting=waiting,
        seen=link,
        only=SimpleNamespace(name_shown=NOT_ENGLISH),
        zone=ZONE,
    )
    # A day's month, as strftime names it until the dates are said in the
    # reader's language (inventory-tng-ist.2).
    return re.sub(rf"\b{DAY:%b}\b", "", shown)


def count_page() -> str:
    """count.html for a retired place, a recount of an item counted by
    its forms and of one in loose units, and the fields for more."""
    item = SimpleNamespace(id=1, name_shown=NOT_ENGLISH, unit=NOT_ENGLISH)
    package = SimpleNamespace(id=1, name_shown=NOT_ENGLISH)
    return signed_in(
        "count.html",
        place=PLACE,
        shown=SimpleNamespace(only=item, search=NOT_ENGLISH, query=link),
        items=[1],
        rows=[
            (item, Decimal(1)),
            (SimpleNamespace(**(vars(item) | {"id": 2})), Decimal(-1)),
        ],
        forms={
            1: [
                SimpleNamespace(
                    field="open_1", words=NOT_ENGLISH, amount=Decimal(1), open_id=1
                )
            ]
        },
        recount={1: (Decimal(1),) * 3, 2: (Decimal(1),) * 3},
        values={},
        problems={},
        others=[item],
        found_packages=[(package, NOT_ENGLISH, NOT_ENGLISH)],
    )


def counts_page() -> str:
    """counts.html, a count and an adjustment among those listed."""
    entry = SimpleNamespace(
        transaction=SimpleNamespace(kind="count", created_at=DAY, time_zone=ZONE),
        lines=[],
        found=True,
        place=NOT_ENGLISH,
        admin=NOT_ENGLISH,
        reason=NOT_ENGLISH,
    )
    return signed_in(
        "counts.html",
        view=VIEW,
        places=[PLACE],
        values={},
        problems={},
        items=[],
        reasons=[],
        counted_places=[],
        kinds=["count", "adjustment"],
        count_kind=counts.COUNT_KIND,
        counters=[],
        recent=[entry],
    )


# What has moved into the catalogue, each as it is shown. Each part of
# moving the words (inventory-tng-oic) adds what it moved, until every
# page is here.
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
    "_retired_holding.html": lambda: render(
        "_retired_holding.html",
        retired_places=[SHELF, SHELF],
        counts_path="/admin/counts",
    ),
    "page.html": lambda: signed_in(
        "page.html",
        tags=SimpleNamespace(styles=[], preloads=[], scripts=[]),
    ),
    "error.html, admin_base.html and _masthead.html": lambda: signed_in(
        "error.html", words=NOT_ENGLISH
    ),
    "_listing.html, narrowed by a search": lambda: from_macros(
        LISTING_PARTS, view=VIEW, listing=listing()
    ),
    "_listing.html, narrowed by a pick": lambda: from_macros(
        LISTING_PARTS, view=VIEW, listing=listing(picked=NOT_ENGLISH, lapsed="inactive")
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
        unit=NOT_ENGLISH,
    ),
    "_retired_record.html and _retired.html": retired_parts,
    "_notice.html": lambda: render(
        "_notice.html",
        notice=NOT_ENGLISH,
        notice_section=None,
        section_id=None,
        undo="/",
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
        default=NOT_ENGLISH,
        again=NOT_ENGLISH,
        units_read=lambda typed: [],
        shown_as=lambda typed, sent: "",
        similar=[
            SimpleNamespace(
                item=SimpleNamespace(id=1, name_shown=NOT_ENGLISH, retired_at=1),
                alias=NOT_ENGLISH,
            ),
            SimpleNamespace(
                item=SimpleNamespace(id=2, name_shown=NOT_ENGLISH, retired_at=None),
                alias=None,
            ),
        ],
    ),
    "over_recorded.html, a row of each kind": recorded_more,
    "count.html, a recount": count_page,
    "counts.html, a count listed": counts_page,
    "code_made.html": lambda: signed_in(
        "code_made.html",
        code=SimpleNamespace(max_uses=2, expires_at=DAY, label=NOT_ENGLISH),
        qr="",
        phrase=NOT_ENGLISH,
        link="/",
        register=NOT_ENGLISH,
        zone=ZONE,
    ),
    "device_revoke.html": lambda: signed_in(
        "device_revoke.html",
        devices=[SimpleNamespace(id=1, name=NOT_ENGLISH)],
        filter={},
        keep="/",
        here="/",
    ),
    "the sign-in pages' forms": lambda: " ".join(
        signed_in(name, **context)
        for name, context in (
            (
                "recover.html",
                {"values": SimpleNamespace(username=NOT_ENGLISH), "problems": {}},
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
                (
                    "confirm.html",
                    {"uses_totp": totp, "retype": list(sign_in.RETYPED.items())},
                )
                for totp in (True, False)
            ),
            *(
                (
                    "link_made.html",
                    {
                        "brought_back": True,
                        "kind": kind,
                        "other": SimpleNamespace(username=NOT_ENGLISH),
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
    assert english(shown) == []
    assert words_shown(shown).strip()


def not_english(session: Session) -> None:
    """Every name the pages show, made not English, so that what is left in
    English on a page is a word not said through the catalogue."""
    # Adjustment reasons too, though the seeded ones stay English
    # (inventory-tng-0z5): they are rows, as names are.
    for model in (Item, ItemPackage, Category, Location, Volunteer, AdjustmentReason):
        for row in session.scalars(select(model)):
            if re.search("[A-Za-z]{2,}", row.name):
                row.name = f"{NOT_ENGLISH} {row.id}"
    for authenticator in session.scalars(select(Authenticator)):
        authenticator.name = NOT_ENGLISH
    for signed_in in session.scalars(select(AdminSession)):
        signed_in.user_agent = NOT_ENGLISH
    for admin in session.scalars(select(Admin)):
        # ASCII, as a username is: one letter, so no word.
        admin.username = f"a{admin.id:04d}"
    session.flush()


# Time zones and what they are called now for short, such as EST.
ZONES = {
    name
    for zone in available_timezones()
    for name in (zone, datetime.now(UTC).astimezone(ZoneInfo(zone)).tzname() or "")
}


def data_words(session: Session) -> set[str]:
    """What a page shows that is the same in every language: unit keys
    (models.UNITS), time zones and their abbreviations, and labels' codes.
    A unit's symbol too (is_a_symbol)."""
    return {*UNITS, *ZONES, *set(session.scalars(select(Label.code)))}


def is_a_symbol(word: str) -> bool:
    """Whether `word` is a unit's symbol as a name shows it, such as Gbps."""
    reading = units.read(word)
    return reading is not None and reading.shown == word


# Every page whose words have all moved, by name: signed in, and signed out.
WORDED = {name: where for page in PAGES.values() for name, where in page.worded.items()}
SIGNED_OUT = [where for page in PAGES.values() for where in page.signed_out]


@pytest.fixture(scope="module")
def world(migrated_template: str) -> Iterator[Built]:
    with built(migrated_template, 0, not_english) as world:
        yield world


@pytest.mark.parametrize("page", WORDED)
def test_a_page_whose_words_have_moved_says_none_in_plain_english(
    page: str, world: Built
) -> None:
    with world.visit() as (client, session):
        where = path(WORDED[page], world.world)
        with speaking(PSEUDO):
            response = client.get(where)
        assert response.status_code == 200, where
        left = {
            word
            for word in english(response.text)
            if not is_a_symbol(word)
            # A new authenticator's key, in groups of four to type.
            and not re.fullmatch("[A-Z2-7]{4}", word)
        }
        assert left - data_words(session) == set()


@pytest.mark.parametrize("where", SIGNED_OUT)
def test_a_page_signed_out_says_none_in_plain_english(
    where: str, client: TestClient
) -> None:
    with speaking(PSEUDO):
        response = client.get(where)
    # A reset link that doesn't work says so, as not found.
    assert response.status_code in {200, 404}, where
    assert english(response.text) == []


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


def test_a_count_is_said_as_a_plural() -> None:
    # Other languages word a count by its number, even where English
    # doesn't: these say the same in either form.
    counts = {
        "%(count)s listed.",
        "All %(count)s",
        "Revoke %(num)d",
    }
    plurals = {m.id[0] for m in read(ENGLISH_PO) if isinstance(m.id, tuple)}
    assert counts <= plurals
    left = [id for id in plurals if id.startswith("%(count)s left")]
    assert len(left) == 2


def test_a_lists_numbers_are_written_as_the_locale_writes_them() -> None:
    """Its sizes, pages and matches, grouped as German groups digits
    (inventory-tng-r9ex); its links keep plain digits (admin/listing.py)."""
    grown = {"sizes": [1000, None], "page": 1000, "pages": 2000}
    view = SimpleNamespace(**vars(VIEW) | grown)
    with speaking(compiled(Catalog(locale="de"))):
        shown = words_shown(
            from_macros(LISTING_PARTS, view=view, listing=listing(found=1500))
        )
    assert "Show 1.000 · All" in shown
    assert "Page 1.000 of 2.000" in shown
    assert "1.500 of 3, matching" in shown
    assert number(1000) == "1,000"


def test_a_language_babel_does_not_know_writes_numbers_as_english() -> None:
    """A pseudo-locale's code, such as en_XA, names no locale Babel knows:
    its numbers read as English's, so its pages still show."""
    header = 'msgid ""\nmsgstr ""\n"Language: en_XA\\n"\n'
    with speaking(compiled(read_po(StringIO(header)))):
        assert number(1000) == "1,000"


def test_a_lists_all_is_said_in_the_locale() -> None:
    with speaking(PSEUDO):
        shown = words_shown(from_macros(LISTING_PARTS, view=VIEW, listing=listing()))
    assert f"{accented('Show')} 25 · {accented('All')}" in shown


def test_cut_kinds_tell_translators_they_are_kinds_of_save() -> None:
    kinds = {str(kind) for kind in length_checks.CUT_KINDS.values()}
    told = {
        m.id
        for m in read(ENGLISH_PO)
        if any("kind of save" in comment for comment in m.auto_comments)
    }
    assert kinds <= told


@pytest.mark.parametrize("lapsed", get_args(Lapsed.__value__))
def test_every_kind_of_lapsed_has_words(lapsed: str) -> None:
    # _listing.html words each in a table: a missing key would say nothing.
    # The row sits in a table, as on a page, or a browser's parser drops it.
    shown = from_macros(
        '{% from "_listing.html" import filters, empty %}'
        "{{ filters(listing) }}<table>{{ empty(listing, 2, '') }}</table>",
        listing=listing(
            lapsed=lapsed,
            show=lapsed,
            narrowed=False,
            search="",
            view=SimpleNamespace(**vars(VIEW) | {"filters": False}),
        ),
    )
    assert markup.links(shown)[1][0]
    assert re.match(r"\w", markup.read(shown).one("td").text)


def test_the_walk_reads_the_words_an_attribute_says() -> None:
    shown = '<h2 title="Hello">x</h2><img alt="Cat"><input placeholder="Type">'
    assert words_shown(shown).split() == ["Hello", "x", "Cat", "Type"]


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


def test_a_unit_name_with_a_prefix_tells_translators_what_it_is() -> None:
    untold = [
        m.id
        for m in read(ENGLISH_PO)
        if isinstance(m.id, str) and "%(prefix)s" in m.id and not m.auto_comments
    ]
    assert untold == []
