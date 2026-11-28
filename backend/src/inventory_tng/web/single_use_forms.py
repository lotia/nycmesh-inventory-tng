"""Forms that make something shown only once: a registration code, recovery
codes, a sign-in link (inventory-tng-8i4.74).

Such a page is the form's own answer, never kept (pages.page), so Back then
Forward offers to send the form again. Each form carries a key made when it
was shown ({{ single_use_field() }}), and its route asks sent_before() first:
sent again, the key is found spent (core/sent_forms.py) and nothing is made,
rather than a second secret that voids the first. A double-click is stopped
before that, in the browser (frontend/src/admin/single-use-forms.ts): it
would show only the second answer, and the first's secret never.
"""

import uuid
from typing import TYPE_CHECKING, Annotated, Any, Literal, Unpack, cast

import structlog
from babel.support import LazyProxy
from fastapi import Form, Request
from fastapi.responses import HTMLResponse
from jinja2 import pass_context
from jinja2.runtime import Context
from markupsafe import Markup
from opentelemetry import metrics
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException

from inventory_tng.core.phrases import lazy_gettext
from inventory_tng.core.refusals import Fields, refusals
from inventory_tng.core.sent_forms import form_key, spend
from inventory_tng.web.pages import page, templates

if TYPE_CHECKING:  # admins.guards imports this module.
    from inventory_tng.admins.guards import SignedIn

log = structlog.get_logger(__name__)

FIELD = "single_use"
# A route's parameter for the key: name it `single_use`.
SingleUseKey = Annotated[str, Form(alias=FIELD)]

# What was made, for the log and the page: a fixed list. "change": what a
# form that showed nothing made, or what one sent again is answered with
# before its route says more (guards.ConfirmedAccountChange).
Made = Literal[
    "registration_code", "registration_codes", "recovery_codes", "link", "change"
]


@refusals(
    log,
    metrics.get_meter(__name__).create_counter(
        "inventory.single_use_forms.refused",
        unit="{form}",
        description="Forms that make something shown once refused, nothing "
        "made: sent again, or with no key, by reason and what they make",
    ),
)
def refused(
    event: str,
    *,
    reason: Literal["sent_again", "key_missing"],
    made: Made,
    **fields: Unpack[Fields],
) -> None: ...


# What the page says of each but "change", which sent_again.html words.
SENT_AGAIN: dict[Made, LazyProxy] = {
    "registration_code": lazy_gettext(
        "That form was already sent, and made a registration code, shown then. "
        "Nothing new was made. It can't be shown again: Back and Forward don't "
        "keep it."
    ),
    "registration_codes": lazy_gettext(
        "That form was already sent, and made registration codes, shown then. "
        "Nothing new was made. They can't be shown again: Back and Forward "
        "don't keep them."
    ),
    "recovery_codes": lazy_gettext(
        "That form was already sent, and made recovery codes, shown then. "
        "Nothing new was made. It can't be shown again: Back and Forward don't "
        "keep it."
    ),
    "link": lazy_gettext(
        "That form was already sent, and made a sign-in link, shown then. "
        "Nothing new was made. It can't be shown again: Back and Forward don't "
        "keep it."
    ),
}
ALREADY_SENT = lazy_gettext("Already sent")


# The page a form was on, which a page saying it was sent before links back
# to: carried in the form, as a browser's settings may leave out Referer.
BACK = "back"


@pass_context
def single_use_field(context: Context) -> Markup:
    """The hidden field with a new key, for a single-use form, and the page
    it is on (BACK). Never filled in by the browser, which may otherwise put
    back a spent key when the form's page is shown again."""
    return Markup(
        '<input type="hidden" name="{}" value="{}" autocomplete="off">'
        '<input type="hidden" name="{}" value="{}">'
    ).format(FIELD, uuid.uuid4(), BACK, context["request"].url.path)


cast(dict[str, Any], templates.env.globals)["single_use_field"] = single_use_field


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
    transaction (spend), so a form refused without a commit can be sent
    again. A key that isn't one is a bad request."""
    key = form_key(typed)
    if key is None:
        refused(
            "form key missing",
            reason="key_missing",
            made=made,
            admin_id=signed.admin.id,
        )
        raise HTTPException(status_code=400)
    if spend(session, key):
        return None
    return sent_again(request, signed, made, back)


def sent_again(
    request: Request, signed: SignedIn, made: Made, back: str
) -> HTMLResponse:
    """The page saying a form was already sent, with a way `back`."""
    refused("form sent again", reason="sent_again", made=made, admin_id=signed.admin.id)
    return page(
        request,
        "sent_again.html",
        {
            "title": ALREADY_SENT,
            "admin": signed.admin,
            "made": made,
            "said": SENT_AGAIN.get(made),
            "back": back,
        },
        409,
    )
