"""Whether a caller with no account may read a volunteer's contact details.

`PUBLIC_VOLUNTEER_DETAILS` is off by default, so an anonymous caller gets a
volunteer's id and display name and nothing else. Signed-in callers are
unaffected either way.

## Why this exists at all, and why the default points here

Decision 0012 point 3 says volunteers do not sign in. Making that true --
`inventory-tng-gnhl` -- opens roughly six endpoints to callers with no account,
and the volunteer pick-list is one of them. Its serializer carries `email` and
`slack_id`, so opening it as it stands would publish a current, checked
name-to-address list of a named organisation's volunteers.

`inventory-tng-81f7` is the question of what that response may contain, it is
not this repository's to answer alone, and it is still open.

**What is settled is narrower: a demonstration full of invented people may show
them freely.** `seed_demo_data` writes made-up volunteers, nobody in it is
real, and none of the harm 81f7 argues about applies to a person who does not
exist. That reasoning is the whole justification and it does not travel, which
is why this is a setting rather than a change to the serializer.

## The default is the careful one, and that is load-bearing

Decision 0021 point 11's pattern, the same as `REQUIRE_SECOND_FACTOR`: the safe
value in the code, the intended one written explicitly in every file this
repository ships. So the deployment nobody thought about withholds, and
publishing takes a line somebody wrote on purpose in a file named for a demo.

The failure this guards against is not somebody arguing for a public roster. It
is nobody arguing anything, and a demo values file being copied to a real
deployment because it is the one known to work.

## What it does not do

It does not gate reading. Anyone may still ask, and `DeviceEnrolmentView` mints
a device token to anybody who asks, so enrolment is no gate either -- that is
what its own docstring means by "the guard is the throttle, not the signature".
This decides only what the answer carries.
"""

# Said on standard error at every start where the roster is public. Decision
# 0021 point 5: adaptation is never silent. It refuses nothing, and it is the
# one thing standing between "we turned this on for a demo in August" and a
# deployment nobody remembers configuring.
ANNOUNCEMENT = (
    "volunteer roster: PUBLIC (PUBLIC_VOLUNTEER_DETAILS is on). "
    "Callers with no account can read every volunteer's email address and Slack ID. "
    "This is safe only where every volunteer is invented -- see docs/deployment.md."
)


def announcement(public: bool) -> str:
    """The line to print at startup, empty when the answer is the careful one.

    Empty for the withholding case on purpose, and
    `inventory_tng.second_factor` gives the reason about announcing ordinary
    configuration.

    IT DOES NOT COUNT THE VOLUNTEERS, though "is this really all invented
    people?" is the question an operator actually wants answered. Settings are
    read before the database is reachable and before migrations have run, so a
    query here would turn a misconfigured or half-built deployment into a crash
    at import. That check belongs where somebody can act on it -- an
    administrator's screen -- rather than in a line printed at boot.
    """
    return ANNOUNCEMENT if public else ""
