"""Whether a password-only account must set up a second factor before it works.

``REQUIRE_SECOND_FACTOR`` is an operator's answer in every environment. There
is no environment gate here, no loopback test and nothing that refuses to
start. Why that is the shape, and why the risk is theirs to weigh rather than
this repository's, is the amendment on
`decision 0013 <../../../docs/decisions/0013-administrator-sign-in.md>`_, which
is where the argument lives and is not repeated here.

Two things about the mechanism that the decision states as requirements and
this module is where they come true:

- The default is ``True``, so a deployment that configures nothing is asked.
- Turning it off changes what is *required* and nothing about what is
  *available*: ``MFA_SUPPORTED_TYPES`` is untouched, ``allauth.mfa`` stays
  installed, and enrolment stays reachable. ``inventory/tests/test_second_factor.py``
  is what fails if that stops being true.
"""

# Said on standard error at every start where the requirement is off, which is
# decision 0021 point 5: adaptation is never silent. It is the whole of the
# nudge, and it refuses nothing.
ANNOUNCEMENT = (
    "second factor: NOT REQUIRED (REQUIRE_SECOND_FACTOR is off). "
    "Accounts signing in with a password can use this deployment without one. "
    "Enrolment is still available to anybody who wants it."
)


def announcement(required: bool) -> str:
    """The line to print at startup, empty when there is nothing surprising.

    Empty for the required case on purpose. A process that announces its
    ordinary configuration teaches the reader to skip its first lines, and
    then the one that matters goes past unread.
    """
    return "" if required else ANNOUNCEMENT
