"""Whether a volunteer needs an account before the application answers them.

`VOLUNTEER_ACCESS` is `session` by default and docs/deployment.md is what an
operator reads. This file is the argument behind it, which that page does not
carry: why a decision already accepted arrives as a switch.

## Why this is a setting rather than a change

Decision 0012 point 3 says volunteers do not sign in, and it is accepted. So
the destination is not in doubt -- `inventory-tng-gnhl` is the issue that gets
there, and this module is it.

What IS still open is `inventory-tng-81f7`: what an anonymous caller may learn
about a person. That question belongs to a consultation with the people whose
names are in the database, not to whoever writes the code, and NYC Mesh is a
volunteer community rather than a customer base -- being able to disagree, and
to fork if disagreement does not resolve, is the point of it working that way.

A setting is what lets both be true at once. The capability exists and can be
demonstrated; no deployment acquires it by accident; and if the consultation
lands somewhere else, what has to be withdrawn is one word in one file rather
than a release.

## The default is today's behaviour, and that is the whole trick

The third setting in this repository shaped that way, and `roster` is where
the shape is argued -- read it there rather than here. What it buys THIS one
is narrower and worth saying: a deployment that upgrades and configures
nothing does not acquire an open catalogue, so nobody discovers decision
0012's posture by finding it already switched on.

## What `open` does NOT open

Every write reserved to an administrator stays reserved: `StaffWrites` and the
step-up of decision 0014 point 5 are untouched, and only `IsAuthenticated` is
replaced. The two appends decision 0012 point 3 names -- a stock transaction
and adding yourself to the volunteer list -- are the only writes that open.

It does not decide what those endpoints SAY, either. `PUBLIC_VOLUNTEER_DETAILS`
already governs what an anonymous caller reads about a person and defaults to
withholding, so turning this on alone opens the pick-list to a stranger as
names without addresses. The two settings are deliberately separate, because
"may a stranger use the scanner" and "may a stranger read your email address"
are different questions with different answers.

## What stands in for the credential

`AnonymousReadThrottle` counts what a caller with no account reads, and
`AppendThrottle` counts what they write. The two are separate buckets on
purpose: a volunteer who has spent an allowance searching for their own name
must still be able to record what they took.

Neither is a defence against copying the catalogue, and the read one says so
at length rather than letting somebody size it as though it were. What it
buys is that the membership question -- does this address belong to a
volunteer -- costs hours instead of seconds.

So `open` is a posture a deployment can be held to, rather than one that
merely works. What it still is NOT is a decision about what an anonymous
caller may learn about a person: that is `inventory-tng-81f7`, and
`PUBLIC_VOLUNTEER_DETAILS` is what withholds in the meantime.
"""

#: Today's behaviour, and the default: an account, as `IsAuthenticated` asks
#: for. Here as a word rather than an absence so that choosing nothing is a
#: choice somebody can read back.
SESSION = "session"
#: Anybody, with nothing presented at all. Decision 0012 point 3's posture.
OPEN = "open"

VALUES = (SESSION, OPEN)

# Standard error, at every start where the surface is open; `roster` cites the
# decision that requires it. It names the OTHER setting rather than only this
# one, because the two together are what somebody actually needs to know: an
# operator who opened the door has read what that does, and may not have
# noticed that what the door leads to is decided elsewhere.
ANNOUNCEMENT = (
    "volunteer access: OPEN (VOLUNTEER_ACCESS is open). "
    "The catalogue, the pick-list, the label map and the code resolver answer callers with no "
    "account. Reads are rate limited (ANONYMOUS_READ_RATE) and what they may say about a person "
    "is PUBLIC_VOLUNTEER_DETAILS. See docs/deployment.md."
)


def chosen(value: str) -> str:
    """One of the two words this setting knows, refusing anything else at boot.

    REFUSED RATHER THAN DEFAULTED, and the direction matters. A deployment that
    wrote ``VOLUNTEER_ACCESS=opened`` means to open, and silently falling back
    to the cautious value would leave somebody demonstrating an application
    that refuses every caller with no clue why. Falling the other way is worse
    still. So neither: it does not start.

    `refusals.rate` makes the same argument about its own setting.
    """
    if value not in VALUES:
        raise ValueError(f"VOLUNTEER_ACCESS={value!r} is not one of: {', '.join(VALUES)}.")
    return value


def announcement(access: str) -> str:
    """What to print at startup; nothing at all when a volunteer signs in.

    Silent for `session` for the reason `inventory_tng.second_factor` gives
    about narrating ordinary configuration.
    """
    return ANNOUNCEMENT if access == OPEN else ""
