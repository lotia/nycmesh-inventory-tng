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
from typing import Annotated, Any, Literal, cast

import structlog
from fastapi import Form, Request
from fastapi.responses import HTMLResponse
from markupsafe import Markup
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException

from inventory_tng.models import SentForm
from inventory_tng.pages import page, templates
from inventory_tng.sign_in import SignedIn

log = structlog.get_logger(__name__)

FIELD = "once"
# A route's parameter for the key: name it `once`.
OnceKey = Annotated[str, Form(alias=FIELD)]

# What was made, for the log and the page: a fixed list.
Made = Literal["registration_code", "recovery_codes", "link"]
WORDS: dict[Made, str] = {
    "registration_code": "a registration code",
    "recovery_codes": "recovery codes",
    "link": "a sign-in link",
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


def form_key(typed: str) -> uuid.UUID | None:
    """A form's key, as its page made it, or None if it isn't one."""
    try:
        return uuid.UUID(typed)
    except ValueError:
        return None


def sent_before(
    request: Request,
    session: Session,
    signed: SignedIn,
    typed: str,
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
    log.info("form sent again", made=made, admin_id=signed.admin.id)
    return page(
        request,
        "sent_again.html",
        {
            "title": "Already sent",
            "admin": signed.admin,
            "made": WORDS[made],
            "back": back,
        },
        409,
    )
