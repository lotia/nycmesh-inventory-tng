"""The credential a device carries so its requests can be told apart, and cut off.

WHAT THIS IS FOR, and the ceiling on it, first -- because everything below is
worth having and none of it is a control.

The network does admission. This deployment sits on hosts whose routing and
firewall rules will not carry traffic originating outside the mesh to the
application at all, and the project owner controls those hosts
(`inventory-tng-2jzx`). So a device credential is not how anybody gets in. It
is ATTRIBUTION: an opaque name a request carries, which is then the rate-limit
bucket, the thing every log line about that request is correlated by, and the
handle a revocation acts on. The owner's words for what that buys are "weakly
guard against in-network bad actors", and that is the whole of the claim.

WHAT SIGNING BUYS AND WHAT IT DOES NOT, because getting this wrong is the
entire risk. A signature stops a token being INVENTED. It does not stop one
being ASKED FOR. `POST /api/devices` answers everybody -- it has to, a device
has nothing to present before it has enrolled -- so somebody who wants fifty
buckets calls it fifty times and every signature checks out. The guard is
therefore never the signature. It is whatever constrains minting, which is two
deliberate things and not an accident of this module: the endpoint is throttled
out of a bucket of its own, and every mint records the address it was asked
from, so fifty devices minted from one address in three minutes is one query
and one bulk revoke rather than an abuse nobody notices.

And a determined insider re-mints. Say that out loud rather than let anybody
infer otherwise from the presence of a signature.

THE REVOCATION RULE, which is the part that stops working silently. The
signature must never be the authorisation on its own: verify it, then read the
row, every request. A future change that skips the select "because the
signature already proved it" removes revocation without failing a single
assertion about a token -- so the test that holds this revokes a device and
asserts the NEXT REQUEST is refused. `inventory.permissions.presented_device`
is the pair, and `inventory.models.Device` is the row.

RULE 3, ROUTE 1, THROUGHOUT. `django.core.signing` mints and checks;
`inventory_tng.debugging` is the module this copies, down to the salt of its
own. Nothing here implements a token format, compares anything by hand, or
implements expiry.
"""

import secrets
from typing import Any

from django.core import signing

# What a device sends. Read through `request.headers` by both of its readers,
# so unlike `debugging` there is no `environ` spelling of it here.
HEADER = "X-Device"

# Separates these signatures from everything else this application signs with
# the same key, so a debug-tracing token cannot be presented as a device and a
# device cannot be presented as one of those. `debugging.SALT` is the other.
SALT = "inventory_tng.devices"


def signer() -> signing.Signer:
    """The signer. Not a `TimestampSigner`, and `SECRET_KEY_FALLBACKS` left in.

    Both of those are the opposite of `inventory_tng.debugging`'s choices, and
    both differ for the same reason: there is a row here.

    NO TIMESTAMP. Nothing here expires, so a stamp would be decoration the
    next reader has to prove is decoration. `Device.revoked_at` is what ends a
    token, or a rotation of the key.

    FALLBACK KEYS LEFT AT DJANGO'S DEFAULT, which is `SECRET_KEY_FALLBACKS`.
    `debugging.signer` empties that list on purpose: rotation is its only
    revocation, so honouring an old key would revoke nothing at the moment
    somebody was rotating BECAUSE a token had leaked. Here per-device
    revocation already exists, which frees rotation to mean whichever of two
    things a deployer needs -- rotate with fallbacks set and nobody is signed
    out, rotate without and every device is, at once, for nothing.
    `docs/deployment.md` is where that lever is written down for whoever would
    reach for it.
    """
    return signing.Signer(salt=SALT)


def new_identifier() -> str:
    """The opaque name a device is known by, and the whole of what is signed.

    Random and meaningless, exactly as `debugging.mint`'s is: it says nothing
    about the person holding the device, which matters because this travels in
    the same channels a name would and ends up on every log line about the
    request.
    """
    return secrets.token_hex(16)


def mint(identifier: str) -> str:
    """The credential a device stores. Signed, never constructed."""
    return signer().sign(identifier)


def presented(token: str) -> str:
    """The identifier inside a token, or nothing at all if it is not honoured.

    Every failure answers alike, for the reason `debugging.minted` gives about
    its own -- and here that covers a key rotated away from as well.

    Answering with an identifier is NOT an answer about whether the device may
    do anything. That is the row; see the revocation rule above.
    """
    if not token.strip():
        return ""
    try:
        return str(signer().unsign(token.strip()))
    except signing.BadSignature:
        return ""


def presented_on(request: Any) -> str:
    """The identifier this request carried, read once however often it is asked.

    TWO CALLERS AND ONE READING. `inventory_tng.context` binds it onto every
    record the request writes, and `inventory.permissions.presented_device`
    needs it before it can look for the row -- so without this the header is
    unsigned twice per request, once in each, for the same answer.

    Memoised on the Django request underneath rather than on whatever wrapper
    asked, because DRF wraps it per request and the capability probe wraps that
    again; `permissions.recently_authenticated` takes the same route and says
    more about why.

    It stays here, in the project package, because it is a question about a
    signature and nothing else. Whether the device may do anything needs a row
    and therefore a model, and this module deliberately cannot see one -- it is
    imported by `settings.py` while Django is still starting.
    """
    underneath: Any = getattr(request, "_request", request)
    answered = getattr(underneath, "_presented_device_identifier", None)
    if answered is None:
        answered = presented(request.headers.get(HEADER, ""))
        underneath._presented_device_identifier = answered
    return str(answered)
