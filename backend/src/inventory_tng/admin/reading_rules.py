"""Unit readings: the reading rules administrators have settled, how a
number and its unit as typed are read (models.ReadingRule,
docs/decisions/0021, 2026-11-15), listed, changed and deleted. A change
reads every name with that unit after a number again, and is refused, naming them, if
two names would then read alike. And the seeding page, which proposes
rules from every name saved (reading_rules.propose), to accept all at
once, change any first, or review one by one (Ali, 2026-11-15)."""

from collections.abc import Mapping, Sequence
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from inventory_tng import items, models, reading_rules, units
from inventory_tng.admin.common import (
    ADMIN,
    ALERTS,
    RULES,
    admin_page,
    form_version,
    text_form,
)
from inventory_tng.db import DbSession, refusal
from inventory_tng.local_time import site_zone
from inventory_tng.models import INTEGER_MAX, Admin, ReadingRule, whole
from inventory_tng.pages import NOTICES, not_found, redirect
from inventory_tng.reading_rules import (
    NOT_A_UNIT,
    chosen_rule,
    offered,
    read_alike_words,
)
from inventory_tng.sign_in import AdminSignedIn, SignedIn

log = structlog.get_logger(__name__)

router = APIRouter(include_in_schema=False)

READINGS = f"{ADMIN}/unit-readings"
SEED = f"{READINGS}/seed"
# The seeding page's field for each token's reading: this, then the token.
PROPOSED = "reading:"
CHOOSE = "Choose how to read each one marked “Choose…” first."
CHOOSE_ONE = "Choose one of the readings offered."

NOTICES.update(
    {
        "reading_saved": "Saved. Every name with it is read so now.",
        "reading_deleted": "Deleted. Names with it are read by the unit table again.",
        "readings_accepted": "Accepted. Every name with them is read so now.",
    }
)


def _list_page(request: Request, session: Session, signed: SignedIn) -> HTMLResponse:
    rows = session.execute(
        select(ReadingRule, Admin.username)
        .outerjoin(Admin, Admin.id == ReadingRule.decided_by_admin_id)
        .order_by(ReadingRule.token)
    ).all()
    return admin_page(
        request,
        signed,
        "reading_rules.html",
        {
            "title": "Unit readings",
            "here": READINGS,
            "rows": rows,
            "zone": site_zone(session),
            "reading_of": units.reading_of,
        },
    )


def _rule_page(
    request: Request,
    session: Session,
    signed: SignedIn,
    rule: ReadingRule,
    *,
    alert: str | None = None,
    read_alike: Sequence[str] = (),
    problem: str | None = None,
) -> HTMLResponse:
    return admin_page(
        request,
        signed,
        "reading_rule.html",
        {
            "title": f"Reading of {rule.token}",
            "here": f"{READINGS}/{rule.id}",
            "back_link": (READINGS, "Unit readings"),
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
    request: Request, signed: AdminSignedIn, session: DbSession
) -> Response:
    return _list_page(request, session, signed)


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
    problem: str | None = None,
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
            "title": "Propose unit readings",
            "here": SEED,
            "back_link": (READINGS, "Unit readings"),
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
    proposals = {proposal.found.key: proposal for proposal in made}
    chosen: dict[str, units.Rule] = {}
    unchosen = False
    for key, value in form.items():
        proposal = proposals.get(key.removeprefix(PROPOSED))
        if not key.startswith(PROPOSED) or proposal is None:
            continue
        rule = chosen_rule(value, proposal.found.offered)
        if rule is None:
            unchosen = True
        else:
            chosen[proposal.found.key] = rule
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
