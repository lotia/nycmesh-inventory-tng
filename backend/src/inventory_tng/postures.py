"""Every setting that changes this application's posture, and which will go.

A setting that alters behaviour and says so at startup is the shape this
repository reaches for whenever a change would otherwise be irreversible or
premature. It is a good shape and it has one failure mode, named by the
project owner on 2026-08-31: an explosion of flags, each individually
justified and none ever removed, because nobody can tell later which were
temporary. Overly complex code arrives by accumulation, and safe defaults do
not prevent it.

So every one of them is written down here, and each is one of two kinds.

## Provisional, and the register's whole reason for existing

A flag that exists to DEFER A DECISION. It is not an operator's choice in any
meaningful sense -- the project simply does not yet know the answer, and would
rather ship the question than guess at it. Each names what would have to be
true for it to be deleted and one value to become the behaviour, so that
whoever settles the decision is handed the cleanup rather than having to
reconstruct which flags it touched.

An entry here is a debt. It is fine to take one on and it is not fine to
forget it.

## Permanent

A flag an operator genuinely chooses between, for ever, where different
deployments will rightly answer differently and no future decision collapses
it. These are not debts and must not be pruned -- listing them is what stops
somebody "tidying up" a real choice on the strength of this file.

The distinction is not always obvious from the setting itself, which is why it
is recorded rather than inferred. ``REQUIRE_SECOND_FACTOR`` looks provisional
and is not: decision 0013's amendment puts the risk deliberately in the
operator's hands, and taking the flag away would be taking a decision back
from them.

## What is enforced

``inventory/tests/test_postures.py`` walks `inventory_tng` for every module
that announces a posture and fails when one is missing from the register, so a
fourth flag cannot arrive unclassified. It also holds every provisional entry
to naming an issue, and holds docs/deployment.md to mentioning every setting
that exists -- because a register nobody can act on is just a longer list.
"""

from dataclasses import dataclass

#: Deletable once something is settled. `retirement` says what.
PROVISIONAL = "provisional"
#: An operator's own answer, for ever. `retirement` says why it is not a debt.
PERMANENT = "permanent"


@dataclass(frozen=True)
class Posture:
    """One setting, and what happens to it in the end."""

    #: The environment variable an operator actually writes.
    setting: str
    #: The module in `inventory_tng` carrying the argument for it.
    module: str
    kind: str
    #: The issue whose settling deletes this flag, and empty for a permanent
    #: one. A FIELD RATHER THAN A SENTENCE, because the prose below names
    #: several issues -- what replaces the flag, what prunes it -- and a test
    #: reading prose cannot tell the deciding one from the others. It could
    #: not, in fact: the first version of this asserted an issue appeared
    #: somewhere in `retirement`, and a mutation that removed the deciding
    #: issue passed because a later sentence mentioned another.
    settled_by: str
    #: For a provisional flag: what would let it be deleted, naming the issue
    #: that settles it. For a permanent one: why it will not be.
    retirement: str
    #: The chart value an operator sets, and empty when no chart renders this
    #: one at all. Recorded rather than derived, because "no chart renders it"
    #: is a fact about the setting worth being able to read back.
    chart_value: str = ""
    #: What the chart's own default renders to. It must be the cautious answer:
    #: it is what a cluster whose operator read nothing runs with.
    chart_default: str = ""
    #: Any value that is NOT the default, used to prove the chart passes an
    #: operator's answer through rather than hard-coding the safe one. Recorded
    #: per setting because there is no deriving it: these are booleans, words,
    #: and one pair (`redacted`/`recorded`) that is neither.
    chart_probe: str = ""
    #: What each shipped Helm example is expected to say, by file name. The
    #: examples exist to make a choice VISIBLE in the file rather than
    #: inherited, so one that stopped setting a value -- or that agreed with
    #: the other -- would be worse than not shipping it at all.
    examples: tuple[tuple[str, str], ...] = ()


REGISTER = (
    Posture(
        setting="VOLUNTEER_ACCESS",
        module="access",
        kind=PROVISIONAL,
        settled_by="inventory-tng-81f7",
        retirement=(
            "inventory-tng-81f7 settles what an anonymous caller may learn about a person. If the "
            "answer is that volunteers do not sign in, delete this and make `open` the behaviour, "
            "which is what decision 0012 point 3 already says. If it is that reads wait for a gate, "
            "delete it the other way and inventory-tng-jro is what replaces it. Either way one "
            "value survives and the setting does not."
        ),
        chart_value="django.volunteerAccess",
        chart_default="session",
        chart_probe="open",
        examples=(("onboarding.yaml", "open"), ("real-data.yaml", "session")),
    ),
    Posture(
        setting="PUBLIC_VOLUNTEER_DETAILS",
        module="roster",
        kind=PROVISIONAL,
        settled_by="inventory-tng-81f7",
        retirement=(
            "inventory-tng-81f7 again, and inventory-tng-81f7.4 is the pruning. This one may not "
            "collapse to a single value -- a demonstration full of invented people and a deployment "
            "holding real ones might rightly differ for ever, and if that is the finding then this "
            "entry moves to permanent rather than being deleted. That is a decision, and it has not "
            "been taken."
        ),
        chart_value="django.publicVolunteerDetails",
        chart_default="false",
        chart_probe="true",
        examples=(("onboarding.yaml", "true"), ("real-data.yaml", "false")),
    ),
    Posture(
        setting="REQUIRE_SECOND_FACTOR",
        module="second_factor",
        kind=PERMANENT,
        settled_by="",
        retirement=(
            "Not a debt. Decision 0013's amendment puts this risk in the operator's hands on "
            "purpose: a more capable application without a second factor is an improvement on what "
            "it replaces, and which deployments need one is not this repository's to decide. "
            "Removing the flag would be taking that decision back."
        ),
        chart_value="django.requireSecondFactor",
        chart_default="true",
        chart_probe="false",
        examples=(("onboarding.yaml", "false"), ("real-data.yaml", "true")),
    ),
    Posture(
        setting="TELEMETRY_PERSONAL_DATA",
        module="redaction",
        kind=PERMANENT,
        settled_by="",
        retirement=(
            "Not a debt. Decision 0021 states the allowlist and the toggle together; what an "
            "operator may re-admit for their own debugging is theirs, under the four conditions "
            "that decision puts on it."
        ),
        chart_value="django.personalData",
        chart_default="redacted",
        chart_probe="recorded",
        examples=(),
    ),
    Posture(
        setting="DJANGO_LOG_LAYOUT",
        module="console",
        kind=PERMANENT,
        settled_by="",
        retirement=(
            "Not a debt, and barely a posture: it decides how wide a log line is drawn for a person "
            "reading one. It is here because it announces itself like the others, and a register "
            "that omitted it would be one somebody could not trust to be complete."
        ),
    ),
)


def provisional() -> tuple[Posture, ...]:
    """The flags that are debts, which is what anybody reading this file wants."""
    return tuple(entry for entry in REGISTER if entry.kind == PROVISIONAL)
