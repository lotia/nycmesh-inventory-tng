"""The register of posture settings is complete, and every debt on it is actionable.

`inventory_tng.postures` carries the argument. What is held here is the part
that decays: a register maintained by hand is a register that is wrong within
two changes, and a wrong one is worse than none because it is trusted.
"""

import importlib
import pkgutil
import re
from pathlib import Path

import pytest

import inventory_tng
from inventory.tests.charts import rendered
from inventory.tests.helpers import shipped
from inventory_tng import postures

#: How an issue is spelled in this repository. A provisional entry that names
#: no issue is a note somebody wrote rather than work anybody will do.
ISSUE = re.compile(r"inventory-tng-[0-9a-z.]+")


def announcing() -> set[str]:
    """Every module in `inventory_tng` that announces a posture at startup.

    THE DISCRIMINATOR IS `announcement`, and it is the right one because it is
    the thing all of these have in common at runtime: a value that changes
    behaviour and is said out loud, per decision 0021 point 5. A module that
    adapts silently is not a posture and is not this register's business.

    Walked rather than listed, which is the entire point: a fourth flag added
    by somebody who has not read `postures` still arrives here.
    """
    found = set()
    for module in pkgutil.iter_modules(inventory_tng.__path__):
        # `settings` imports the world and is what CONSUMES these rather than
        # being one; importing it here is both circular and pointless.
        if module.name == "settings":
            continue
        if callable(getattr(importlib.import_module(f"inventory_tng.{module.name}"), "announcement", None)):
            found.add(module.name)
    return found


def test_every_module_that_announces_a_posture_is_on_the_register() -> None:
    """The assertion the whole register stands on."""
    registered = {entry.module for entry in postures.REGISTER}

    missing = sorted(announcing() - registered)
    assert not missing, (
        f"{missing} change this application's posture and say so at startup, and nothing records "
        "whether they are a debt or an operator's own choice: add each to inventory_tng.postures "
        "with what would let it be deleted, or with why it will never be"
    )


def test_nothing_on_the_register_has_gone_away() -> None:
    """The other direction, which is how a register rots quietly.

    A deleted flag leaves its entry behind, the entry reads as outstanding
    work, and somebody eventually spends an afternoon on a debt that was
    already paid.
    """
    stale = sorted({entry.module for entry in postures.REGISTER} - announcing())

    assert not stale, f"{stale} no longer announce a posture; take the entry out rather than leaving it"


def test_every_entry_is_one_kind_or_the_other() -> None:
    wrong = sorted(
        entry.setting for entry in postures.REGISTER if entry.kind not in (postures.PROVISIONAL, postures.PERMANENT)
    )

    assert not wrong, f"{wrong} are neither provisional nor permanent, so nobody can tell what to do with them"


def test_every_debt_names_the_issue_that_settles_it() -> None:
    """A provisional flag with no issue is a flag nobody will ever remove.

    This is the assertion that makes the register worth keeping. "We will tidy
    this up later" is how the pile the project owner warned about is built;
    naming the issue is what turns it into work somebody is handed.

    ASKED OF `settled_by` AND NOT OF THE PROSE, and `Posture` says why: the
    prose names several issues and a search across it cannot tell which one
    decides. That version of this test passed a mutation it should have
    caught.
    """
    unactionable = sorted(entry.setting for entry in postures.provisional() if not ISSUE.fullmatch(entry.settled_by))

    assert not unactionable, (
        f"{unactionable} are provisional and name no issue in `settled_by`, so nothing connects them "
        "to the decision they are waiting on: name it, or say why the flag is permanent instead"
    )


def test_a_permanent_entry_is_waiting_on_nothing() -> None:
    """The classification and the field have to agree.

    An entry called permanent while naming a deciding issue is one of the two
    wrong, and which one it is matters: it is either a debt somebody
    misfiled, or a stale pointer at a decision that no longer applies.
    """
    contradictory = sorted(
        entry.setting for entry in postures.REGISTER if entry.kind == postures.PERMANENT and entry.settled_by
    )

    assert not contradictory, (
        f"{contradictory} are on the register as an operator's choice for ever and also name an issue "
        "that would settle them; one of the two is wrong"
    )


def test_a_permanent_entry_says_why_rather_than_only_that() -> None:
    """Because "permanent" is the classification somebody reaches for to avoid the work."""
    bare = sorted(
        entry.setting for entry in postures.REGISTER if entry.kind == postures.PERMANENT and len(entry.retirement) < 80
    )

    assert not bare, f"{bare} claim to be an operator's choice for ever without arguing it"


@pytest.mark.parametrize("entry", postures.REGISTER, ids=lambda entry: entry.setting)
def test_an_operator_can_find_out_what_each_one_does(entry: postures.Posture) -> None:
    """Every posture is explained where the person choosing it is reading.

    check-config.sh already fails when a value rendered into a container is
    absent from docs/deployment.md, so most of this is held elsewhere. What is
    NOT held there is `DJANGO_LOG_LAYOUT`, which no chart renders -- and a
    register that quietly covered four of five would be the kind nobody checks.
    """
    explained = shipped(Path("docs/deployment.md")) + shipped(Path("docs/observability.md"))

    assert entry.setting in explained, (
        f"{entry.setting} changes what this application does and neither operator document mentions "
        "it, so the only way to find out is to read the source"
    )


# ---------------------------------------------------------------------------
# Written down in every file that ships, for every posture at once
# ---------------------------------------------------------------------------
#
# ONE COPY, DRIVEN BY THE REGISTER. These four assertions used to live three
# times over, in test_second_factor.py, test_roster_visibility.py and
# test_volunteer_access.py, with nothing but the constants differing --
# inventory-tng-aoji.3. A fourth setting meant a fourth copy, and whoever
# wrote it would have written two of the four and not noticed the others were
# missing, because nothing said what the full set was.
#
# Now a setting gets this coverage by being REGISTERED, which is the same act
# that records whether it is a debt. The behaviour tests stay in their own
# files; what moved is only the family that asks whether a choice is visible
# in the files this repository ships rather than inherited from the code.

#: The pair that decides what a local stack actually runs with, as opposed to
#: what a cluster does. test_trusted_origins.py argues why both matter.
SHIPPED = ("compose.yaml", ".env.sample")

#: The entries a chart renders. `DJANGO_LOG_LAYOUT` is not one: it is a
#: property of the terminal a process is writing to, decided per process
#: rather than per deployment, so there is nothing for an operator to set.
CHARTED = [entry for entry in postures.REGISTER if entry.chart_value]


@pytest.mark.parametrize("entry", CHARTED, ids=lambda entry: entry.setting)
@pytest.mark.parametrize("a_file", SHIPPED)
def test_every_shipped_configuration_states_each_posture(a_file: str, entry: postures.Posture) -> None:
    """What a fresh clone runs with is chosen here rather than fallen back to.

    ASKED OF THE CHARTED ONES. `DJANGO_LOG_LAYOUT` is absent from
    `compose.yaml` correctly: it describes the terminal a process is writing
    to, and a container has none, so there is nothing for a deployment to say.
    That it belongs in `.env.sample` at all is check-config's business.
    """
    assert entry.setting in shipped(Path(a_file)), (
        f"{a_file} does not mention {entry.setting}, so what a fresh clone runs with is whatever the "
        "code fell back to rather than something this repository chose"
    )


@pytest.mark.parametrize("entry", CHARTED, ids=lambda entry: entry.setting)
def test_the_chart_renders_each_posture_at_its_cautious_default(entry: postures.Posture) -> None:
    supplied = rendered()

    assert entry.setting in supplied, (
        f"the chart does not put {entry.setting} in the backend's environment, so a cluster has no way "
        f"to make this choice without editing the chart"
    )
    assert supplied[entry.setting]["value"] == entry.chart_default, (
        f"the chart's default for {entry.setting} is no longer {entry.chart_default!r}, so a cluster "
        f"whose operator read nothing runs with {supplied[entry.setting]['value']!r}"
    )


@pytest.mark.parametrize("entry", [e for e in CHARTED if e.chart_probe], ids=lambda entry: entry.setting)
def test_each_chart_value_carries_the_operator_answer_rather_than_a_constant(entry: postures.Posture) -> None:
    """A template that hard-coded the cautious value passes the test above.

    That is the shape worth a second render: rendering the safe default and
    ignoring the operator looks identical to rendering it and honouring them,
    right up to the deployment that will not do what it was told.

    `chart_probe` rather than a rule for inverting a default, because these
    are booleans, words, and one pair (`redacted`/`recorded`) that is neither.
    A rule that guessed wrong would render a value the chart does not accept,
    and the failure would say nothing about the question being asked.
    """
    asked = entry.chart_probe

    supplied = rendered(**{entry.chart_value: asked})

    assert supplied[entry.setting]["value"] == asked, (
        f"the chart renders {entry.setting} without reading {entry.chart_value}, so an operator's "
        f"answer never reaches the application; it rendered {supplied[entry.setting]['value']!r}"
    )


@pytest.mark.parametrize("entry", [e for e in CHARTED if e.examples], ids=lambda entry: entry.setting)
def test_each_example_states_the_answer_it_is_named_for(entry: postures.Posture) -> None:
    """Both starting points choose, and they choose oppositely.

    An example that dropped a line would inherit the cautious answer and look
    fine, which is why the assertion is on the words rather than on behaviour.
    """
    for example, expected in entry.examples:
        text = shipped(Path("infra/helm/inventory-tng/examples") / example)
        leaf = entry.chart_value.split(".")[-1]

        assert f"{leaf}: {expected}" in text, (
            f"{example} no longer says {leaf}: {expected}, so the starting point named for this answer "
            "does not carry it"
        )
