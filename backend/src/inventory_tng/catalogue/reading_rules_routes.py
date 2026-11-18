"""Unit readings: the reading rules administrators have settled, how a
number and its unit as typed are read (models.ReadingRule,
docs/decisions/0021, 2026-11-15), listed, changed and deleted. A change
reads every name with that unit after a number again, and is refused, naming them, if
two names would then read alike. And the seeding page, which proposes
rules from every name saved (reading_rules.propose), to accept all at
once, change any first, or review one by one (Ali, 2026-11-15)."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session

from inventory_tng import models
from inventory_tng.admin.common import ADMIN, admin_page, text_form
from inventory_tng.admin.listing import (
    AskedView,
    Sort,
    Sorting,
    View,
    arranged,
    tidied,
)
from inventory_tng.admins.guards import AdminSignedIn, SignedIn
from inventory_tng.catalogue import items, queries, reading_rules, units
from inventory_tng.catalogue.forms import CHOOSE, CHOOSE_ONE, PROPOSED, seed_choices
from inventory_tng.catalogue.reading_rules import (
    NOT_A_UNIT,
    chosen_rule,
    offered,
    read_alike_words,
)
from inventory_tng.core.db import DbSession, refusal
from inventory_tng.core.local_time import site_zone
from inventory_tng.core.phrases import Words, gettext, lazy_gettext
from inventory_tng.models import INTEGER_MAX, ReadingRule, whole
from inventory_tng.pages import NOTICES, not_found, redirect
from inventory_tng.web.forms import ALERTS, RULES, form_version

log = structlog.get_logger(__name__)

router = APIRouter(include_in_schema=False)

READINGS = f"{ADMIN}/unit-readings"
SEED = f"{READINGS}/seed"

READING_SAVED = lazy_gettext("Saved. Every name with it is read so now.")
READING_DELETED = lazy_gettext(
    "Deleted. Names with it are read by the unit table again."
)
READINGS_ACCEPTED = lazy_gettext("Accepted. Every name with them is read so now.")
NOTICES.update(
    {
        "reading_saved": READING_SAVED,
        "reading_deleted": READING_DELETED,
        "readings_accepted": READINGS_ACCEPTED,
    }
)
NOT_A_UNIT_WORDS = lazy_gettext("Not a unit")
RESPELT = lazy_gettext("Respelt")
AS_TYPED = lazy_gettext("As typed")
THE_IMPORT = lazy_gettext("The import")
UNIT_READINGS = lazy_gettext("Unit readings")
PROPOSE_TITLE = lazy_gettext("Propose unit readings")


def reading_title(token: str) -> str:
    return gettext("Reading of %(token)s", token=token)


@dataclass(frozen=True)
class _Row:
    """A reading rule as the list shows it, in words."""

    rule: ReadingRule
    read_as: Words
    saved: Words
    by: Words


def _row(rule: ReadingRule, username: str | None) -> _Row:
    reading = units.reading_of(rule.reading) if rule.reading else None
    return _Row(
        rule,
        f"{reading.shown} · {reading.kind_words}" if reading else NOT_A_UNIT_WORDS,
        RESPELT if rule.respell else AS_TYPED,
        username or THE_IMPORT,
    )


# How the list sorts: by the unit, as it always has, whatever its case, or
# by a heading, each by its words as shown; the newest first by When (Ali,
# 2026-11-15, inventory-tng-qwb).
SORTING = Sorting(
    "unit",
    {
        "unit": Sort.keyed(lambda row: (row.rule.token.lower(), row.rule.token)),
        "read_as": Sort.keyed(lambda row: row.read_as.lower()),
        "saved": Sort.keyed(lambda row: row.saved),
        "by": Sort.keyed(lambda row: row.by.lower()),
        "when": Sort.keyed(lambda row: row.rule.decided_at, first="desc"),
    },
)
# What the settled-by filter offers: by the import, or by an administrator.
SETTLED_BY = {
    "import": ReadingRule.decided_by_admin_id.is_(None),
    "admin": ReadingRule.decided_by_admin_id.is_not(None),
}


def _rule_page(
    request: Request,
    session: Session,
    signed: SignedIn,
    rule: ReadingRule,
    *,
    alert: Words | None = None,
    read_alike: Sequence[str] = (),
    problem: Words | None = None,
) -> HTMLResponse:
    return admin_page(
        request,
        signed,
        "reading_rule.html",
        {
            "title": reading_title(rule.token),
            "here": f"{READINGS}/{rule.id}",
            "back_link": (READINGS, UNIT_READINGS),
            "rule": rule,
            "offered": offered(rule.token, rule.rule),
            "not_a_unit": NOT_A_UNIT,
            "alert": alert,
            "read_alike": read_alike,
            "problem": problem,
        },
        409 if alert or read_alike else 400 if problem else None,
    )


@router.get(READINGS)
def list_readings(
    request: Request,
    signed: AdminSignedIn,
    session: DbSession,
    asked: AskedView,
    by: str = "",
    unit: str = "",
) -> Response:
    """The rules, filtered by who settled them and by what the unit holds,
    as typed in any case (Ali, 2026-11-15)."""
    unit = tidied(unit)
    settled = SETTLED_BY.get(by)
    filters = {"by": by if settled is not None else "", "unit": unit}
    view = View.read(READINGS, SORTING, asked, filters=filters)
    found = queries.rules_with_deciders(
        session, *(() if settled is None else (settled,))
    )
    rows, view = arranged(
        (
            _row(rule, name)
            for rule, name in found
            if unit.lower() in rule.token.lower()
        ),
        view,
    )
    log.debug("readings listed", found=view.found, sort=view.by, page=view.page)
    return admin_page(
        request,
        signed,
        "reading_rules.html",
        {
            "title": UNIT_READINGS,
            "here": READINGS,
            "rows": rows,
            "view": view,
            "zone": site_zone(session),
        },
    )


@router.get(f"{READINGS}/{{rule_id:int}}")
def reading(
    request: Request, signed: AdminSignedIn, session: DbSession, rule_id: int
) -> Response:
    found = session.get(ReadingRule, rule_id)
    if found is None:
        return not_found()
    return _rule_page(request, session, signed, found)


def _refused(
    request: Request,
    session: Session,
    signed: SignedIn,
    rule: ReadingRule,
    broken: str,
    change: units.Rule | None,
) -> HTMLResponse:
    """The rule's page again, saying why a change to it was refused: names
    it would read alike, by id, or one of ALERTS."""
    if broken != items.READ_ALIKE:
        return _rule_page(request, session, signed, rule, alert=RULES[broken])
    # What the change would have read, the savepoint undone.
    rules = {**models.reading_rules(session)}
    if change is None:
        rules.pop(rule.token, None)
    else:
        rules[rule.token] = change
    words = read_alike_words(session, [rule.token], rules)
    log.debug("reading refused", reading_rule_id=rule.id, read_alike=len(words))
    return _rule_page(request, session, signed, rule, read_alike=words)


@router.post(f"{READINGS}/{{rule_id:int}}")
def save_reading(
    request: Request,
    signed: AdminSignedIn,
    session: DbSession,
    rule_id: int,
    reading: Annotated[str, Form()] = "",
    respell: Annotated[str, Form()] = "",
    version: Annotated[str, Form()] = "",
) -> Response:
    found = session.get(ReadingRule, rule_id)
    if found is None:
        return not_found()
    offers = offered(found.token, found.rule)
    change = chosen_rule(reading, offers, respell=respell == "on")
    if change is None:
        return _rule_page(request, session, signed, found, problem=CHOOSE_ONE)
    seen = form_version(version)
    _, broken = refusal(
        session,
        lambda: reading_rules.change(
            session, found, change, signed.admin.id, seen=seen
        ),
        items.READ_ALIKE,
        *ALERTS,
    )
    if broken is not None:
        return _refused(request, session, signed, found, broken, change)
    session.commit()
    log.info("reading rule saved", reading_rule_id=rule_id)
    return redirect(f"{READINGS}/{rule_id}", notice="reading_saved")


@router.post(f"{READINGS}/{{rule_id:int}}/delete")
def delete_reading(
    request: Request,
    signed: AdminSignedIn,
    session: DbSession,
    rule_id: int,
    version: Annotated[str, Form()] = "",
) -> Response:
    found = session.get(ReadingRule, rule_id)
    if found is None:
        return not_found()
    seen = form_version(version)
    _, broken = refusal(
        session,
        lambda: reading_rules.delete(session, found, seen=seen),
        items.READ_ALIKE,
        *ALERTS,
    )
    if broken is not None:
        return _refused(request, session, signed, found, broken, None)
    session.commit()
    log.info("reading rule deleted", reading_rule_id=rule_id)
    return redirect(READINGS, notice="reading_deleted")


def _seed_page(
    request: Request,
    session: Session,
    signed: SignedIn,
    *,
    one: bool,
    at: int,
    sent: Mapping[str, str] | None = None,
    problem: Words | None = None,
    read_alike: Sequence[str] = (),
    proposals: list[reading_rules.Proposal] | None = None,
) -> HTMLResponse:
    """The seeding page: `proposals`, if already made, else made now."""
    if proposals is None:
        proposals = _proposals(session)
    at = max(0, min(at, len(proposals) - 1))
    return admin_page(
        request,
        signed,
        "reading_seed.html",
        {
            "title": PROPOSE_TITLE,
            "here": SEED,
            "back_link": (READINGS, UNIT_READINGS),
            "proposals": proposals[at : at + 1] if one else proposals,
            "total": len(proposals),
            "one": one,
            "at": at,
            "sent": sent or {},
            "field": PROPOSED,
            "not_a_unit": NOT_A_UNIT,
            "problem": problem,
            "read_alike": read_alike,
        },
        400 if problem else 409 if read_alike else None,
    )


def _proposals(session: Session) -> list[reading_rules.Proposal]:
    """What the seeding page proposes, from every name, by the rules held."""
    names = reading_rules.names(session)
    return reading_rules.propose(names, models.reading_rules(session))


@router.get(SEED)
def seed(
    request: Request,
    signed: AdminSignedIn,
    session: DbSession,
    one: str = "",
    at: str = "",
) -> Response:
    """Every unit after a number no rule settles, proposed: all, or one by one
    from `at`."""
    first = whole(at, 0, INTEGER_MAX) or 0
    return _seed_page(request, session, signed, one=bool(one), at=first)


@router.post(SEED)
def accept(
    request: Request,
    signed: AdminSignedIn,
    session: DbSession,
    form: Annotated[dict[str, str], Depends(text_form)],
) -> Response:
    """The readings sent, each kept as a rule: refused, saying so, while any
    is not chosen, or if names would then read alike. One settled since is
    left as it is."""
    one, at = bool(form.get("one")), whole(form.get("at", ""), 0, INTEGER_MAX) or 0
    rules = models.reading_rules(session)
    made = _proposals(session)
    chosen, unchosen = seed_choices(form, made)
    if unchosen:
        return _seed_page(
            request,
            session,
            signed,
            one=one,
            at=at,
            sent=form,
            problem=CHOOSE,
            proposals=made,
        )
    written, broken = refusal(
        session,
        lambda: reading_rules.decide(session, chosen, signed.admin.id),
        items.READ_ALIKE,
    )
    if broken is not None:
        words = read_alike_words(session, chosen, {**rules, **chosen})
        log.debug("readings refused", read_alike=len(words))
        return _seed_page(
            request,
            session,
            signed,
            one=one,
            at=at,
            sent=form,
            read_alike=words,
            proposals=made,
        )
    session.commit()
    log.info(
        "reading rules set",
        reading_rule_ids=[rule.id for rule in written or ()],
        by="seed",
    )
    back = {"one": "1", "at": str(at)} if one else {}
    return redirect(SEED, notice="readings_accepted", **back)
