"""Forms that make something shown only once: a registration code, recovery
codes, a sign-in link (inventory-tng-8i4.74).

Such a page is the form's own answer, never kept (pages.page), so Back then
Forward offers to send the form again. Each form carries a key made when it
was shown ({{ once_field() }}), and its route asks sent_before() first: sent
again, the key is found spent and nothing is made, rather than a second
secret that voids the first. A double-click is stopped before that, in the
browser (admin_base.html): it would show only the second answer, and the
first's secret never.
"""

import uuid
from typing import TYPE_CHECKING, Annotated, Any, Literal, cast

import structlog
from fastapi import Form, Request
from fastapi.responses import HTMLResponse
from markupsafe import Markup
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException

from inventory_tng.db import exists_where
from inventory_tng.models import SentForm
from inventory_tng.pages import page, templates

if TYPE_CHECKING:  # sign_in imports this module.
    from inventory_tng.sign_in import SignedIn

log = structlog.get_logger(__name__)

FIELD = "once"
# A route's parameter for the key: name it `once`.
OnceKey = Annotated[str, Form(alias=FIELD)]

# What was made, for the log and the page: a fixed list. "change": what a
# form that showed nothing made, or what one sent again is answered with
# before its route says more (sign_in.ConfirmedAccountChange).
Made = Literal["registration_code", "recovery_codes", "link", "change"]
WORDS: dict[Made, str] = {
    "registration_code": "a registration code",
    "recovery_codes": "recovery codes",
    "link": "a sign-in link",
    "change": "its change",
}


def once_field() -> Markup:
    """The hidden field with a new key, for a form to send once. Never
    filled in by the browser, which may otherwise put back a spent key when
    the form's page is shown again."""
    return Markup(
        '<input type="hidden" name="{}" value="{}" autocomplete="off">'
    ).format(FIELD, uuid.uuid4())


cast(dict[str, Any], templates.env.globals).update(
    once_field=once_field, once_name=FIELD
)


def form_key(typed: object) -> uuid.UUID | None:
    """A form's key, as its page made it, or None if it isn't one."""
    if not isinstance(typed, str):
        return None
    try:
        return uuid.UUID(typed)
    except ValueError:
        return None


def sent_before(
    request: Request,
    session: Session,
    signed: SignedIn,
    typed: object,
    made: Made,
    back: str,
) -> HTMLResponse | None:
    """None if the form with this key is sent for the first time; else the
    page saying so, with a way `back`. The key is spent in the request's
    transaction, so a form refused without a commit can be sent again, and
    a second sending at once waits on the first, then finds it. A key that
    isn't one is a bad request."""
    key = form_key(typed)
    if key is None:
        log.info("form key missing", made=made, admin_id=signed.admin.id)
        raise HTTPException(status_code=400)
    added = session.execute(
        insert(SentForm)
        .values(key=key)
        .on_conflict_do_nothing(index_elements=["key"])
        .returning(SentForm.key)
    )
    if added.first() is not None:
        return None
    return sent_again(request, signed, made, back)


def spent(session: Session, typed: object) -> bool:
    """Whether the form with this key was sent before, without spending it:
    so one sent again is answered before a code is spent on it."""
    key = form_key(typed)
    return key is not None and exists_where(session, SentForm.key == key)


def sent_again(
    request: Request, signed: SignedIn, made: Made, back: str
) -> HTMLResponse:
    """The page saying a form was already sent, with a way `back`."""
    log.info("form sent again", made=made, admin_id=signed.admin.id)
    return page(
        request,
        "sent_again.html",
        {
            "title": "Already sent",
            "admin": signed.admin,
            "made": made,
            "words": WORDS[made],
            "back": back,
        },
        409,
    )
