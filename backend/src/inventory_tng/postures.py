"""The five settings a consultation is going to watch, and what each may say.

PROVISIONAL, ALL OF IT. Nothing here is a decision this project has taken. It
is scaffolding for one demo, built so that `inventory-tng-81f7` -- what an
anonymous caller may learn about a person -- can be argued from something a
room has watched rather than from prose. `inventory-tng-81f7.4` is already
filed to take every one of these back out again once that question is settled,
and that bead is where the two outcomes it may choose between are written.

So the names are the thing to hold on to. Each of the five is spelled the same
way everywhere it appears -- here, in `settings.py`, in `.env.sample`, in the
permission class or serializer that reads it, and in the tests -- because the
pruner's acceptance is a grep per name that comes back empty. Two of them
carry a value the posture needs beside them, and both are named as a SUFFIX of
the setting they serve -- `VOLUNTEER_ACCESS_CODE`, `VOLUNTEER_ACCESS_NETWORKS`
-- so that grep reaches them too.

DEFAULTS ARE TODAY'S BEHAVIOUR, every one of them, and `.env.sample` says what
each of those is. That is what makes the demo affordable: it is five values
rather than a fork, and nothing moves for anybody who sets none of them.

WHAT THE CREDENTIAL IS, and this is the part not to mistake for a security
control. `enrolled_self` and `enrolled_code` mint an OPAQUE TOKEN with
`django.core.signing`, exactly as `inventory_tng.debugging` mints one, and
nothing here implements a token format, a comparison or anything resembling a
primitive -- AGENTS.md rule 3, route 1. Why that is the right thing to build
for this and the wrong thing to mistake for anything else is on the other end
of it, in `frontend/src/device/credential.ts`.
"""

import functools
import ipaddress
import secrets

from django.core import signing
from django.utils.crypto import constant_time_compare

# --------------------------------------------------------------------------
# ANONYMOUS_PAYLOAD. .env.sample carries what each word means to whoever is
# choosing one; what is here is what the code does with it.
# --------------------------------------------------------------------------

#: Today's behaviour: display name, email address and Slack ID, as
#: `VolunteerSerializer` has always answered.
FULL = "full"
#: The address blurred by the rule that was measured. See `masked` below.
MASKED = "masked"
#: Id and display name, and nothing that identifies a person off this list.
NAMES_ONLY = "names_only"

ANONYMOUS_PAYLOAD_VALUES = (FULL, MASKED, NAMES_ONLY)

# --------------------------------------------------------------------------
# VOLUNTEER_ACCESS
# --------------------------------------------------------------------------

#: Today's behaviour, and the default: a session, as `IsAuthenticated` asks
#: for now. Not one of the four postures the consultation compares -- it is
#: what the application does before anybody chooses one, and it is here so that
#: choosing nothing is a value rather than an absence.
SESSION = "session"
#: Anybody, with nothing at all presented. This is the posture
#: `inventory-tng-gnhl` would arrive at.
OPEN = "open"
#: A device credential, which any device may mint for itself.
ENROLLED_SELF = "enrolled_self"
#: A device credential, minted only on presenting VOLUNTEER_ACCESS_CODE.
ENROLLED_CODE = "enrolled_code"
#: A request from one of VOLUNTEER_ACCESS_NETWORKS.
MESH_ONLY = "mesh_only"

VOLUNTEER_ACCESS_VALUES = (SESSION, OPEN, ENROLLED_SELF, ENROLLED_CODE, MESH_ONLY)

#: The two postures that ask a device to enrol before it may read.
ENROLLING = (ENROLLED_SELF, ENROLLED_CODE)

# What `/api/me` says about enrolment, which is the one thing a client cannot
# work out from a 403. "You may not" and "you have not yet" are the same status
# code and want opposite screens: the first is a wall and the second is a
# button. The four words below are the whole vocabulary.
#
#: Nothing to enrol. Either this deployment gates on something else, or on
#: nothing -- so a refusal here means "not you" and there is no screen to show.
NOT_REQUIRED = "not_required"
#: A credential is asked for and this caller is carrying a good one.
ENROLLED = "enrolled"
#: Asked for, not carried, and any device may mint one.
ENROL_SELF = "self"
#: Asked for, not carried, and minting one needs the code from the room.
ENROL_WITH_CODE = "code"

ENROLMENT_STATES = (NOT_REQUIRED, ENROLLED, ENROL_SELF, ENROL_WITH_CODE)

# --------------------------------------------------------------------------
# CUSTODY_VISIBILITY
# --------------------------------------------------------------------------

#: Disclosed to anybody.
ANONYMOUS = "anonymous"
#: Today's behaviour, and the default: only to somebody signed in.
IDENTIFIED = "identified"

CUSTODY_VISIBILITY_VALUES = (ANONYMOUS, IDENTIFIED)

# --------------------------------------------------------------------------
# SEARCH_MINIMUM
# --------------------------------------------------------------------------

#: Nought is today's behaviour: the bare collection is the whole first page.
SEARCH_MINIMUM_VALUES = (0, 1, 2, 3)


def chosen(setting: str, value: str, allowed: tuple[str, ...]) -> str:
    """One of the words this setting knows, refusing anything else at boot.

    Refused rather than defaulted, for the reason `refusals.rate` gives about
    its own: a deployment given a posture other than the one it asked for, and
    not told, discovers it in front of the room. A typo here is the one thing
    that cannot be repaired between two sentences on stage, which is what the
    whole arrangement exists to make possible.
    """
    if value not in allowed:
        raise ValueError(f"{setting}={value!r} is not one of: {', '.join(allowed)}.")
    return value


def search_minimum(value: int) -> int:
    """How many characters the pick-list waits for, refusing a number it is not."""
    if value not in SEARCH_MINIMUM_VALUES:
        raise ValueError(
            f"SEARCH_MINIMUM={value!r} is not one of: {', '.join(str(number) for number in SEARCH_MINIMUM_VALUES)}."
        )
    return value


def enrolment_code(access: str, code: str) -> str:
    """The code a device presents, refusing an empty one where it is the gate.

    An empty one under `enrolled_code` admits nobody while looking exactly
    like a posture that works: the screen appears, and every code typed into it
    is refused. Stopped at boot instead, naming the variable. Why there is no
    default at all is with the variable in .env.sample.
    """
    if access == ENROLLED_CODE and not code.strip():
        raise ValueError(
            "VOLUNTEER_ACCESS=enrolled_code needs VOLUNTEER_ACCESS_CODE set to the code "
            "people will be given. Empty, it would refuse every device and look like it worked."
        )
    return code.strip()


def networks(access: str, listed: list[str]) -> list[str]:
    """The ranges `mesh_only` admits, checked at boot rather than per request.

    Same argument as the code above: an unparseable range refuses everybody and
    an empty list refuses everybody, and both look from the outside like the
    posture working. So a malformed one stops the process naming the variable.

    Hands back the STRINGS rather than the parsed networks, and that is a
    correction rather than a preference. A setting holding parsed objects is a
    setting nothing can state: `override_settings(VOLUNTEER_ACCESS_NETWORKS=
    ["10.69.0.0/16"])` -- which is what anybody would write, in a test or in a
    shell -- then raised `TypeError` inside the permission class and answered
    500 across the whole API. `parsed` below is where the strings become
    networks, once.
    """
    for entry in listed:
        try:
            ipaddress.ip_network(entry.strip(), strict=False)
        except ValueError as refused:
            raise ValueError(
                f"VOLUNTEER_ACCESS_NETWORKS carries {entry!r}, which is not a network: {refused}"
            ) from None
    if access == MESH_ONLY and not listed:
        raise ValueError(
            "VOLUNTEER_ACCESS=mesh_only needs VOLUNTEER_ACCESS_NETWORKS set to the ranges "
            "that may read. Empty, it would refuse everybody and look like it worked."
        )
    return [entry.strip() for entry in listed]


@functools.cache
def parsed(listed: tuple[str, ...]) -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    """The ranges as networks, built once per distinct list.

    Cached because a permission class asks per request and the answer is a
    function of the setting; keyed on a tuple so it is hashable. Anything that
    will not parse is dropped rather than raised on: boot has already refused
    that list, so reaching here with one means a value was substituted after
    the fact, and refusing everybody is the safe reading of it.
    """
    built = []
    for entry in listed:
        try:
            built.append(ipaddress.ip_network(str(entry).strip(), strict=False))
        except ValueError:
            continue
    return tuple(built)


def within(address: str, listed: list[str] | tuple[str, ...]) -> bool:
    """Whether this client address falls in one of the ranges.

    Which address, and what makes it trustworthy, is `client_address` in
    `inventory.permissions`; this only decides membership. An address that will
    not parse is in no range, so a caller whose forwarded header is nonsense --
    or one this deployment has decided it cannot trust -- is refused rather
    than admitted.
    """
    try:
        client = ipaddress.ip_address(address.strip())
    except ValueError:
        return False
    return any(client in network for network in parsed(tuple(listed)))


# --------------------------------------------------------------------------
# The mask, which is carried DESPITE having been measured out.
# --------------------------------------------------------------------------

#: What stands in for the characters the mask drops. Bullets rather than
#: asterisks because the room is reading them off a projector, and a fixed
#: count rather than the real length because the length is itself a
#: distinguisher -- which would make the demo's two identical rows an accident
#: of two addresses happening to be the same size.
LOCAL_FILLER = "•" * 3
DOMAIN_FILLER = "•" * 4


def masked(address: str | None) -> str | None:
    """An address blurred by the exact rule `inventory-tng-81f7` measured.

    THE RULE IS `local[0] + domain-initial + TLD`, and it is reproduced here
    character for character rather than improved on, because the figures on
    that bead were measured against it and the demo has to show the room the
    thing the numbers are about. Act two puts two seeded volunteers on screen
    rendering as the identical string; a mask that kept one more character
    would separate them and quietly contradict the measurement.

    Kept despite having been measured out, and that is the point of it. The
    measurement says a mask fails to separate 5 of 13 colliding names and
    collapses 60 addresses to 35 distinct forms; four seconds of two identical
    rows settles that in a room in a way a percentage does not.

    Nothing is decided from the output: it is rendered, never compared, so
    there is no timing to worry about and nothing here is a credential.
    """
    if address is None:
        return None
    local, separator, domain = address.strip().rpartition("@")
    if not separator:
        # Not an address this rule can describe. The model's own EmailField
        # will not store one, so this is a row edited underneath us rather
        # than an ordinary case -- and answering with a mask made of nothing
        # is better than answering with the value.
        return f"{LOCAL_FILLER}@{DOMAIN_FILLER}"
    head, dot, tail = domain.rpartition(".")
    # A domain with no dot at all keeps none: `admin@localhost` is storable --
    # Django's EmailValidator allowlists it -- and a dangling separator on the
    # end of a mask is a character that says nothing about anybody.
    return f"{local[:1]}{LOCAL_FILLER}@{domain[:1]}{DOMAIN_FILLER}{dot}{tail if head else ''}"


# --------------------------------------------------------------------------
# The device credential. An opaque token and nothing more; the module header
# says why that is the right thing to build for this and the wrong thing to
# mistake for a security control.
# --------------------------------------------------------------------------

#: What an enrolled device sends, and what it looks like once WSGI has had it.
DEVICE_HEADER = "X-Device"

#: Separates these signatures from everything else this application signs with
#: the same key, exactly as `inventory_tng.debugging.SALT` does for its own.
DEVICE_SALT = "inventory_tng.postures.device"


def device_signer() -> signing.TimestampSigner:
    """The signer, with `SECRET_KEY_FALLBACKS` explicitly out of it.

    `inventory_tng.debugging.signer` argues the empty `fallback_keys` and the
    argument carries over unchanged. There is a stored row here as well, so
    rotation is not the only revocation available -- but the two must not
    disagree about what a rotation means.
    """
    return signing.TimestampSigner(salt=DEVICE_SALT, fallback_keys=[])


def new_device_identifier() -> str:
    """The opaque name a device is known by, and the whole of what is signed.

    Random and meaningless, like `debugging.mint`'s: it says nothing about the
    person holding the device, which matters because this travels through the
    same channels a name would.
    """
    return secrets.token_hex(16)


def mint_device_token(identifier: str) -> str:
    """The credential a device stores. Signed and stamped, never constructed."""
    return device_signer().sign(identifier)


def presented_device(token: str) -> str:
    """The identifier inside a token, or nothing at all if it is not honoured.

    One answer for every way a token can fail, for the reason
    `debugging.minted` gives about its own.

    No `max_age`, deliberately, and the frontend's `device/credential.ts`
    carries the argument for both ends of that. What ends this one instead is
    the row being revoked, or the key being rotated.
    """
    if not token.strip():
        return ""
    try:
        return str(device_signer().unsign(token.strip()))
    except signing.BadSignature:
        return ""


def code_matches(offered: str, expected: str) -> bool:
    """Whether the code a device typed is the one this deployment was given.

    `django.utils.crypto.constant_time_compare` and nothing else -- AGENTS.md
    rule 3 names it first for exactly this, and comparing two strings by hand
    is the "constant-time comparison" that rule forbids in as many words.

    An empty expected code matches nothing, whatever was offered. Boot refuses
    that arrangement outright (see `enrolment_code`), so this is the second
    guard rather than the only one.
    """
    if not expected:
        return False
    return constant_time_compare(offered.strip(), expected)
