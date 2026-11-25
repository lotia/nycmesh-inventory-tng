"""The server's words, read through Babel's catalogue (phrases.py,
docs/decisions/0022), in Python and in the templates."""

import importlib
import pkgutil
import re
import subprocess
import sys
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
from bs4 import NavigableString, Tag
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

import inventory_tng
from inventory_tng.admins import (
    accounts,
    activity,
    admins_routes,
    guards,
    mail_messages,
)
from inventory_tng.admins.reset_routes import RESET_NOTICE
from inventory_tng.catalogue import reading_rules, reading_rules_routes, units
from inventory_tng.catalogue.forms import (
    ALIAS_TAKEN,
    TAKEN_FIELDS,
    fraction_held,
    read_as_one,
    short_to_set,
)
from inventory_tng.core import mail
from inventory_tng.core.phrases import (
    BRAND,
    ENGLISH_PO,
    Words,
    compiled,
    gettext,
    joined,
    lazy_gettext,
    lazy_pgettext,
    ngettext,
    number,
    pgettext,
    read,
    speaking,
    writing_numbers,
)
from inventory_tng.devices import devices_routes
from inventory_tng.devices.forms import days_problem, uses_problem
from inventory_tng.labels import labels
from inventory_tng.models.admins import Admin, AdminSession, Authenticator
from inventory_tng.models.catalogue import UNITS, Category, Item, ItemPackage
from inventory_tng.models.labels import Label
from inventory_tng.models.places import Location
from inventory_tng.models.site_settings import SignInSettings
from inventory_tng.models.stock import TRANSACTION_KINDS, AdjustmentReason
from inventory_tng.models.volunteers import Volunteer
from inventory_tng.site_settings.forms import WHOLE, whole_minutes
from inventory_tng.stock import (
    count_gone_areas,
    count_groups,
    count_routes,
    counts_routes,
    length_checks_routes,
    over_recorded_queries,
    over_recorded_routes,
    stock_routes,
    used_up_routes,
)
from inventory_tng.stock.count_forms import NONE_GONE
from inventory_tng.stock.moments import Moment
from inventory_tng.volunteers import volunteers_routes
from inventory_tng.web import formatting
from inventory_tng.web.admin_pages import revoke_title
from inventory_tng.web.forms import at_most_places, name_up_to, up_to
from inventory_tng.web.listing import Lapsed
from inventory_tng.web.navigation import retired_holding
from inventory_tng.web.pages import templates

from helpers import markup
from helpers.locales import GERMAN
from helpers.swept_pages import NOT_ENGLISH, PAGES, Built, built, path

# Each letter accented, so English left out of the catalogue shows plain.
ACCENTS = str.maketrans(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ",
    "àƀćďēƒĝĥĩĵķĺɱńōƥʠŕśţũʋŵẋŷźÀƁĆĎĒƑĜĤĨĴĶĹḾŃŌƤɊŔŚŢŨṼŴẊŶŹ",
)
# A message's placeholders, and a literal per cent, kept as they are.
PLACEHOLDER = re.compile(r"(%\(\w+\)[sd]|%%)")
# Those, a tag and a character reference: what a translator keeps.
KEPT = re.compile(rf"({PLACEHOLDER.pattern[1:-1]}|<[^>]*>|&\w+;)")


def accented(message: str) -> str:
    parts = KEPT.split(message)
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
    table (a dict, tuple or list, such as pages._NOTICES), in every module."""
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
    lambda: name_up_to(9),
    lambda: accounts.password_problem("", "") or "",
    lambda: accounts.password_problem("." * 1000, "") or "",
    lambda: up_to(9),
    lambda: at_most_places(3),
    lambda: revoke_title(NOT_ENGLISH),
    *(
        lambda said=said: said(NOT_ENGLISH, NOT_ENGLISH)
        for said in activity.WORDS.values()
    ),
    *(lambda said=said: said(NOT_ENGLISH) for said in TAKEN_FIELDS.values()),
    *(lambda said=said: said(NOT_ENGLISH) for said in ALIAS_TAKEN.values()),
    lambda: read_as_one(NOT_ENGLISH),
    lambda: fraction_held(ItemPackage(name_shown=NOT_ENGLISH, size=Decimal("30.5"))),
    lambda: fraction_held(
        ItemPackage(
            name_shown=NOT_ENGLISH, size=Decimal(30), short_below=Decimal("1.5")
        )
    ),
    lambda: short_to_set(ItemPackage(size=Decimal(30), short_below=Decimal("1.5"))),
    *(lambda said=said: said(1, NOT_ENGLISH) for said in reading_rules.NAMED.values()),
    lambda: reading_rules.both(NOT_ENGLISH, NOT_ENGLISH),
    lambda: reading_rules.near(NOT_ENGLISH),
    lambda: reading_rules_routes.reading_title(NOT_ENGLISH),
    lambda: volunteers_routes.merge_title(NOT_ENGLISH, NOT_ENGLISH),
    lambda: admins_routes.link_title(NOT_ENGLISH),
    lambda: admins_routes.reset_title(NOT_ENGLISH),
    lambda: uses_problem(9),
    lambda: days_problem(9),
    lambda: devices_routes.revoke_many_title(1),
    lambda: devices_routes.revoke_many_title(3),
    *(lambda said=said: said(1, 9) for said in WHOLE.values()),
    lambda: whole_minutes(1, 9),
    lambda: used_up_routes.close_title(NOT_ENGLISH),
    lambda: count_routes.count_title(NOT_ENGLISH),
    lambda: count_routes.moved_meanwhile(0),
    lambda: count_routes.moved_meanwhile(1),
    lambda: count_routes.moved_meanwhile(3),
    lambda: count_groups.sealed_how_many(NOT_ENGLISH),
    lambda: count_groups.opened_left(NOT_ENGLISH, "2026-11-15"),
    lambda: count_gone_areas.opened_here_since(Decimal(250), NOT_ENGLISH),
    lambda: count_gone_areas.opened_then_moved(NOT_ENGLISH),
    lambda: count_gone_areas.opened_then_taken_out(NOT_ENGLISH),
    lambda: stock_routes.recorded_more_view(2),
    lambda: over_recorded_routes.place_brought_back(NOT_ENGLISH),
    lambda: over_recorded_routes.not_changed(NOT_ENGLISH),
    lambda: over_recorded_routes.waiting_view(2),
    *(
        lambda said=said: said(NOT_ENGLISH, NOT_ENGLISH)
        for said in over_recorded_routes.RETIRED_EMPTY.values()
    ),
    lambda: formatting.times(2, NOT_ENGLISH),
    lambda: over_recorded_queries.from_an_open_package(NOT_ENGLISH),
    lambda: over_recorded_queries.sealed(NOT_ENGLISH),
    lambda: over_recorded_queries.opened(NOT_ENGLISH),
    lambda: over_recorded_queries.loose(NOT_ENGLISH),
    lambda: over_recorded_queries.held_words(NOT_ENGLISH),
    lambda: over_recorded_queries.adjusted_at(NOT_ENGLISH),
    lambda: over_recorded_queries.end_only(NOT_ENGLISH),
    lambda: over_recorded_queries.closed_on_day(NOT_ENGLISH, NOT_ENGLISH),
    lambda: over_recorded_queries.counted_on(NOT_ENGLISH, NOT_ENGLISH),
    lambda: labels.package_label(NOT_ENGLISH, NOT_ENGLISH, NOT_ENGLISH),
    *(lambda said=said: said("") for said in units.NAMES.values()),
]


def all_mail() -> list[mail.Message]:
    """Every mail, each branch of it, with values that are not English."""
    return [
        mail_messages.reset_link(NOT_ENGLISH, 1, NOT_ENGLISH),
        *(
            build(NOT_ENGLISH, 1)
            for build in (
                mail_messages.reset_link_made,
                mail_messages.invited,
                mail_messages.restored,
                mail_messages.recovery_link_made,
                mail_messages.invited_by_operator,
                mail_messages.let_in,
                mail_messages.password_changed,
                mail_messages.email_changed,
                mail_messages.authenticators_retired,
                mail_messages.left_without_a_factor,
                mail_messages.retired,
                mail_messages.recovery_codes_made,
                mail_messages.recovery_code_used(0),
                mail_messages.recovery_code_used(1),
                mail_messages.recovery_code_used(3),
                mail_messages.recovery_code_used(9),
                mail_messages.authenticators_changed(added=True),
                mail_messages.authenticators_changed(added=False),
                mail_messages.activity(NOT_ENGLISH),
                *(
                    mail_messages.sign_in_settings_changed(
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
SAID_IN = ("title", "alt", "placeholder", "aria-label", "aria-description")


def words_shown(html: str) -> str:
    """`html`'s words people read or hear, hidden for now or not: its text
    and what its tags say in attributes, but not its scripts or styles."""
    said: list[str] = []
    for node in markup.read(html).descendants:
        if isinstance(node, markup.Element):
            said += [node.attrs[name] for name in SAID_IN if name in node.attrs]
        elif (
            isinstance(node, NavigableString)
            and type(node) in Tag.MAIN_CONTENT_STRING_TYPES
        ):
            said.append(node)
    return " ".join(" ".join(said).split())


def english(html: str) -> list[str]:
    """The words `html` shows in plain English: those with two letters of
    the English alphabet in a row, not counting the app's name, the same in
    every language."""
    shown = words_shown(html).replace(BRAND, "")
    words = re.findall(r"[\w/+-]+", shown)
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
    # The page's decimal separator, as web/pages.page gives every page.
    "decimal": ".",
}


def signed_in(name: str, **context: object) -> str:
    """Template `name`, rendered signed in with `context`."""
    return render(name, **SIGNED_IN, **context)


VIEW = SimpleNamespace(
    aria_sort=lambda _column: "descending",
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
    filtered=lambda _name: "",
    pages=3,
    page=2,
    link=link,
)


def listing(**differs: object) -> SimpleNamespace:
    """A long list with search (web/listing.py), as _listing.html reads it."""
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
  with context %}{% from "stock/_retired.html" import retired %}
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
    """A unit in a name, as units.found_in reads it (catalogue/_units_read.html)."""
    return SimpleNamespace(
        **{
            "key": NOT_ENGLISH,
            "typed": NOT_ENGLISH,
            "asks": False,
            "offered": [
                READING,
                SimpleNamespace(called=NOT_ENGLISH, kind_words=NOT_ENGLISH),
            ],
            "shown": lambda _reading: NOT_ENGLISH,
            "read": READING,
            "clue": [NOT_ENGLISH, NOT_ENGLISH],
            "rule": SimpleNamespace(respell=True),
        }
        | differs
    )


def units_read() -> str:
    """Each way catalogue/_units_read.html says a unit was read, or asks."""
    each = [
        unit_read(asks=True),
        unit_read(),
        unit_read(clue=[], rule=None),
        unit_read(read=None),
    ]
    return render(
        "catalogue/_units_read.html",
        units_read=lambda _typed: each,
        shown_as=lambda _typed, _sent: NOT_ENGLISH,
        typed=NOT_ENGLISH,
        sent={},
        not_a_unit="",
    )


def recorded(**differs: object) -> SimpleNamespace:
    """A line recorded with more than was moved, as stock/over_recorded.html
    shows it (over_recorded_queries.Shown)."""
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
            "made_loose": None,
        }
        | differs
    )


def recorded_more() -> str:
    """stock/over_recorded.html, a row of each kind, and a refusal."""
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
        recorded(moved_whole=SimpleNamespace(count=Decimal(1), words=NOT_ENGLISH)),
        *(
            recorded(one_counted=SimpleNamespace(**one, how=how))
            for how in ("counted", "closed")
        ),
        *(
            recorded(both_right=SimpleNamespace(**both, arrived_how=how))
            for how in ("counted", "closed")
        ),
        recorded(
            made_loose=SimpleNamespace(
                open=NOT_ENGLISH,
                on=DAY,
                place_id=1,
                place=NOT_ENGLISH,
                countable=True,
            )
        ),
        recorded(from_place=None),
        recorded(to_place=None),
    ]
    refused = SimpleNamespace(shown=waiting[1], words=NOT_ENGLISH, place=PLACE, at=None)
    shown = signed_in(
        "stock/over_recorded.html",
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
    """stock/count.html for a retired place, a recount of an item counted by
    its forms and of one in loose units, and the fields for more."""
    item = SimpleNamespace(id=1, name_shown=NOT_ENGLISH, unit=NOT_ENGLISH)
    package = SimpleNamespace(id=1, name_shown=NOT_ENGLISH)
    return signed_in(
        "stock/count.html",
        place=PLACE,
        shown=SimpleNamespace(only=item, search=NOT_ENGLISH, query=link),
        moment=Moment(0, 0),
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
        gone=NONE_GONE,
    )


def counts_page() -> str:
    """stock/counts.html, a count and an adjustment among those listed."""
    entry = SimpleNamespace(
        transaction=SimpleNamespace(kind="count", created_at=DAY, time_zone=ZONE),
        lines=[],
        found=True,
        place=NOT_ENGLISH,
        admin=NOT_ENGLISH,
        reason=NOT_ENGLISH,
    )
    return signed_in(
        "stock/counts.html",
        view=VIEW,
        places=[PLACE],
        values={},
        problems={},
        items=[],
        reasons=[],
        counted_places=[],
        kinds=["count", "adjustment"],
        count_kind=counts_routes.COUNT_KIND,
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
    "frontend_page.html": lambda: signed_in(
        "frontend_page.html",
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
    "stock/_forms.html": lambda: from_macros(
        '{% from "stock/_forms.html" import forms_lines %}{{ forms_lines(f, unit) }}',
        f=FORMS,
        unit=NOT_ENGLISH,
    ),
    "_retired_record.html and stock/_retired.html": retired_parts,
    "_notice.html": lambda: render(
        "_notice.html",
        notice=NOT_ENGLISH,
        notice_section=None,
        section_id=None,
        undo="/",
    ),
    "_shown_once.html": lambda: render("_shown_once.html"),
    "admins/_code_field.html": lambda: " ".join(
        render("admins/_code_field.html", code_totp=totp, totp_only=only, limits={})
        for totp, only in ((True, True), (True, False), (False, False))
    ),
    "admins/_totp_fields.html": lambda: render(
        "admins/_totp_fields.html",
        values={},
        problems={},
        limits={},
        totp_optional=True,
    ),
    "catalogue/_units_read.html": units_read,
    "catalogue/_item_name.html, with similar names": lambda: render(
        "catalogue/_item_name.html",
        values={},
        problems={},
        default=NOT_ENGLISH,
        again=NOT_ENGLISH,
        units_read=lambda _typed: [],
        shown_as=lambda _typed, _sent: "",
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
    "stock/over_recorded.html, a row of each kind": recorded_more,
    "stock/count.html, a recount": count_page,
    "stock/counts.html, a count listed": counts_page,
    **{
        f"devices/code_made.html, {count} made": (
            lambda count=count: signed_in(
                "devices/code_made.html",
                codes=[
                    (
                        SimpleNamespace(
                            id=1, max_uses=2, expires_at=DAY, label=NOT_ENGLISH
                        ),
                        NOT_ENGLISH,
                        "/",
                        "",
                    )
                ]
                * count,
                register=NOT_ENGLISH,
                zone=ZONE,
            )
        )
        for count in (1, 4)
    },
    "devices/device_revoke.html": lambda: signed_in(
        "devices/device_revoke.html",
        devices=[SimpleNamespace(id=1, name=NOT_ENGLISH)],
        filter={},
        keep="/",
        here="/",
    ),
    "the sign-in pages' forms": lambda: " ".join(
        signed_in(name, **context)
        for name, context in (
            (
                "admins/recover.html",
                {"values": SimpleNamespace(username=NOT_ENGLISH), "problems": {}},
            ),
            ("admins/setup.html", {"off": True}),
            (
                "admins/setup.html",
                {"values": {}, "problems": {}, "totp_optional": True},
            ),
            ("admins/recovery_codes.html", {"codes": [1]}),
            (
                "admins/authenticator_new.html",
                {"required": True, "values": {}, "problems": {}},
            ),
            *(
                (
                    "admins/reset.html",
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
                    "admins/confirm.html",
                    {"uses_totp": totp, "retype": list(guards.RETYPED.items())},
                )
                for totp in (True, False)
            ),
            *(
                (
                    "admins/link_made.html",
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
    named = (Item, ItemPackage, Category, Location, Volunteer, AdjustmentReason)
    for model in (*named, Authenticator):
        for row in session.scalars(select(model)):
            if re.search("[A-Za-z]{2,}", row.name):
                row.name = f"{NOT_ENGLISH} {row.id}"
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
    (models.catalogue.UNITS), time zones and their abbreviations, and labels'
    codes. A unit's symbol too (is_a_symbol)."""
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
    assert ngettext("%(num)s box", "%(num)s boxes", 2) == "2 boxes"
    assert ngettext("%(num)s %(what)s", "%(num)s %(what)ss", 1, what="box") == "1 box"
    assert str(lazy_gettext("Half: 50%%")) == "Half: 50%"


def test_a_context_tells_two_meanings_of_the_same_english_apart() -> None:
    catalogue = Catalog()
    catalogue.add("To", "Into", context="place")
    catalogue.add("To", "Recipient", context="mail")
    with speaking(compiled(catalogue)):
        assert pgettext("place", "To") == "Into"
        assert str(lazy_pgettext("mail", "To")) == "Recipient"
    assert pgettext("place", "To") == "To"


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
        "Revoke %(num)s",
    }
    plurals = {m.id[0] for m in read(ENGLISH_PO) if isinstance(m.id, tuple)}
    assert counts <= plurals
    left = [id for id in plurals if id.startswith("%(count)s left")]
    assert len(left) == 2


def test_a_lists_numbers_are_written_as_the_locale_writes_them() -> None:
    """Its sizes, pages and matches, grouped as German groups digits
    (inventory-tng-r9ex); its links keep plain digits (web/listing.py)."""
    grown = {"sizes": [1000, None], "page": 1000, "pages": 2000}
    view = SimpleNamespace(**vars(VIEW) | grown)
    with speaking(GERMAN):
        shown = words_shown(
            from_macros(LISTING_PARTS, view=view, listing=listing(found=1500))
        )
    assert "Show 1.000 · All" in shown
    assert "Page 1.000 of 2.000" in shown
    assert "1.500 of 3, matching" in shown


def test_a_language_babel_does_not_know_writes_numbers_as_english() -> None:
    """A pseudo-locale's code, such as en_XA, names no locale Babel knows:
    its numbers read as English's, so its pages still show."""
    header = 'msgid ""\nmsgstr ""\n"Language: en_XA\\n"\n'
    with speaking(compiled(read_po(StringIO(header)))):
        assert number(1000) == "1,000"


def test_names_are_joined_as_the_locale_joins_a_list() -> None:
    """Shelf C and Bin 4 in English, und in German (inventory-tng-ygk5)."""
    assert joined(["Shelf C"]) == "Shelf C"
    assert joined(["Shelf C", "Bin 4"]) == "Shelf C and Bin 4"
    with speaking(GERMAN):
        assert joined(["Shelf C", "Bin 4"]) == "Shelf C und Bin 4"


def items_page(total: int) -> str:
    """catalogue/items.html, its list summing up `total` items."""
    return signed_in(
        "catalogue/items.html",
        listing=listing(total=total, narrowed=False, search="", show="current"),
        all_categories=[],
        category_names={},
        categories=[],
        units=[],
        values={},
        problems={},
        here="/here",
        units_read=lambda _typed: [],
        shown_as=lambda _typed, _sent: "",
    )


def test_a_count_is_written_as_the_locale_writes_it_its_form_chosen_by_number() -> None:
    """A plural's count, in Python and in a template, whether named count
    or num (inventory-tng-31q3)."""
    revoke = {"filter": {}, "keep": "/", "here": "/"}
    many = [SimpleNamespace(id=1, name=NOT_ENGLISH)] * 1000
    hint = "admins/_password_hint.html"
    assert retired_holding(1000) == "1,000 retired places hold stock"
    assert retired_holding(1) == "1 retired place holds stock"
    assert "1,000 items" in words_shown(items_page(1000))
    assert "1 item" in words_shown(items_page(1))
    assert "Revoke 1,000" in signed_in(
        "devices/device_revoke.html", devices=many, **revoke
    )
    assert "at least 1 character<" in render(
        hint, limits=SimpleNamespace(password_min=1)
    )
    with speaking(GERMAN):
        assert retired_holding(1000) == "1.000 retired places hold stock"
        assert "1.000 items" in words_shown(items_page(1000))
        assert "at least 1.000 characters" in render(
            hint, limits=SimpleNamespace(password_min=1000)
        )
        assert mail_messages.days(1000) == "1.000 days"


def test_a_number_in_a_message_is_written_as_the_locale_writes_it() -> None:
    """Not only a plural's count: a view's count and a field's limits too
    (inventory-tng-31q3)."""
    assert stock_routes.recorded_more_view(1000) == "Recorded more (1,000)"
    assert over_recorded_routes.waiting_view(1000) == "Waiting (1,000)"
    assert up_to(1000) == "Up to 1,000 characters."
    assert whole_minutes(1, 10080) == "A whole number of minutes, from 1 to 10,080."
    with speaking(GERMAN):
        assert uses_problem(1000) == "From 1 to 1.000 devices."


def test_numbers_in_words_on_a_comma_page_take_its_comma_ungrouped() -> None:
    """A plural's count and a message's number alike, until the block
    ends (Ali, 2026-11-23, inventory-tng-4mc3)."""
    with writing_numbers(","):
        assert retired_holding(1000) == "1000 retired places hold stock"
        assert up_to(1500) == "Up to 1500 characters."
        assert number(Decimal("1.5")) == "1,5"
        assert "1000 items" in words_shown(items_page(1000))
    assert up_to(1500) == "Up to 1,500 characters."


def test_the_home_and_seeding_pages_write_their_counts_as_the_locale_does() -> None:
    """Needs your action's total, each section's count, and the seeding
    page's place in its proposals (inventory-tng-1axu)."""
    kind = SimpleNamespace(title=NOT_ENGLISH, path="/admin/stock/x")
    home = signed_in(
        "admins/admin_home.html",
        warnings=[],
        needs=[SimpleNamespace(kind=kind, items=[], total=1000)],
        changes=[],
        retired_places=[],
        current={"/admin/stock": 2000},
        paths=SimpleNamespace(account="/here"),
    )
    found = SimpleNamespace(key="g", offered=[])
    proposal = SimpleNamespace(found=found, count=1, names=[], why="", proposed=None)
    seeding = signed_in(
        "catalogue/reading_seed.html",
        proposals=[proposal],
        field="unit_",
        sent={},
        not_a_unit="none",
        one=True,
        at=999,
        total=2000,
        here="/here",
    )
    assert "(1,000)" in home
    assert ": 2,000" in home
    assert "1,000 of 2,000." in seeding
    assert markup.box_values(seeding, "at") == ["999"]


def test_only_an_id_fills_a_messages_placeholder_as_plain_digits() -> None:
    """A number people read is filled as number() writes it, a string, so
    a message's only %d is an id, which stays plain digits as in a link."""
    placeholders = {
        name
        for message in read(ENGLISH_PO)
        for text in forms(message)
        for name in re.findall(r"%\((\w+)\)d", text)
    }
    assert placeholders <= {"id"}


def test_a_badge_counts_as_the_locale_writes_numbers() -> None:
    badge = SimpleNamespace(count=1000, words=NOT_ENGLISH)
    sections = [("/admin/stock", NOT_ENGLISH, badge)]
    page = render("_masthead.html", **(SIGNED_IN | {"sections": sections}))
    assert markup.read(page).all("span", "1,000", aria_hidden="true")


def test_a_lists_all_is_said_in_the_locale() -> None:
    with speaking(PSEUDO):
        shown = words_shown(from_macros(LISTING_PARTS, view=VIEW, listing=listing()))
    assert f"{accented('Show')} 25 · {accented('All')}" in shown


def test_cut_kinds_tell_translators_they_are_kinds_of_save() -> None:
    kinds = {str(kind) for kind in length_checks_routes.CUT_KINDS.values()}
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
    assert lone_per_cents("%(num)s of 50%%") == []


def test_no_message_has_a_lone_per_cent() -> None:
    # Every message is %-formatted when said, so a lone % would fail the page.
    catalogue = read(ENGLISH_PO)
    said = [form for message in catalogue if message.id for form in forms(message)]
    assert said
    assert [form for form in said if lone_per_cents(form)] == []


def test_every_kind_of_save_but_loosen_has_words_for_a_cut() -> None:
    # queries.recent_cuts leaves loosen out: it has no words to show.
    assert length_checks_routes.CUT_KINDS.keys() == set(TRANSACTION_KINDS) - {"loosen"}


def test_a_unit_name_with_a_prefix_tells_translators_what_it_is() -> None:
    untold = [
        m.id
        for m in read(ENGLISH_PO)
        if isinstance(m.id, str) and "%(prefix)s" in m.id and not m.auto_comments
    ]
    assert untold == []


@pytest.mark.parametrize(
    "module",
    [
        "inventory_tng.core.phrases",
        "inventory_tng.web.formatting",
        "inventory_tng.labels.labels",
        "inventory_tng.stock.over_recorded",
    ],
)
def test_rules_read_phrases_without_loading_jinja(module: str) -> None:
    """Only templates need Jinja (web/template_phrases.py), so rules reading
    phrases leave it unloaded. Run in a fresh interpreter, which has loaded
    nothing else."""
    shown = subprocess.run(
        [
            sys.executable,
            "-c",
            f"import sys, {module}; print('jinja2' in sys.modules)",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert shown.stdout.strip() == "False"
