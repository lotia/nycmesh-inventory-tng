"""Every rate this chart renders reaches the pod, and carries the operator's answer.

A limit that does not reach the container is not a limit, and the failure is
silent in the permissive direction: the application falls back to whatever
`settings.py` declares, the cluster runs looser than the values file says, and
nothing anywhere says so.

NOTHING HELD THIS UNTIL `inventory-tng-k9gr.1`. Every throttling test assigns
its rate through the `settings` fixture, so all of them stay green while the
chart renders a rate to nowhere. `inventory-tng-81f7.1` added the pair of
assertions for one rate and left four beside it without, which is the
asymmetry this file removes.

THE LIST COMES FROM THE APPLICATION, NOT FROM THE CHART, and the difference
is the whole test. Reading it from `_helpers.tpl` was the first attempt and it
was self-defeating: deleting a rate from the chart deleted the case that
would have caught it, and hard-coding a value stopped the entry matching the
pattern, so BOTH mutations passed while running one test fewer. A guard whose
expectations come from the thing it is guarding cannot fail.

So the expectation is `settings.env.scheme`: every name the application reads
as a rate. The chart has to satisfy that, and a rate leaving the chart now
fails rather than vanishing.
"""

import re
from pathlib import Path

import pytest

from inventory.tests.charts import rendered
from inventory.tests.helpers import shipped

CHART = Path("infra/helm/inventory-tng/templates/_helpers.tpl")

#: A value no default is, so honouring the operator is told apart from
#: rendering a constant that happens to match. Shaped like a DRF rate because
#: several of these are parsed as one, and a value the application would refuse
#: makes a test fail for the wrong reason.
UNMISTAKABLE = "7/hour"


def declared_rates() -> list[str]:
    """Every name this application reads as a rate, from its own schema."""
    from inventory_tng import settings

    found = sorted(name for name in settings.env.scheme if name.endswith("_RATE"))
    assert len(found) >= 5, f"only {found} were declared, which is too few to be all of them"
    return found


def chart_value_for(setting: str) -> str:
    """The `.Values` path the chart renders that setting from, or "" for none."""
    found = re.search(rf"- name: {setting}\n\s*value: \{{\{{ \.Values\.(django\.\w+)", shipped(CHART))
    return found.group(1) if found else ""


@pytest.mark.parametrize("setting", declared_rates())
def test_every_rate_arrives_in_the_container(setting: str) -> None:
    """Rendered at all, and at something the application can read.

    An empty string is the shape worth naming: Helm renders a missing value as
    one, `quote` makes it `""`, and the manifest looks correct in a diff while
    the pod reads no limit at all.
    """
    supplied = rendered()

    assert setting in supplied, (
        f"the chart does not put {setting} in the backend's environment, so a cluster runs on whatever "
        "settings.py fell back to and the values file decides nothing"
    )
    assert supplied[setting]["value"], (
        f"the chart renders {setting} as an empty string, which the application reads as no limit rather "
        "than as the number the values file states"
    )


@pytest.mark.parametrize("setting", declared_rates())
def test_every_rate_carries_the_operator_answer(setting: str) -> None:
    """A template that hard-coded its default passes the test above.

    test_postures.py makes the same argument about the settings it covers. What
        is particular here is when it bites: somebody tightens a limit in response
        to something happening, and it goes on not applying.
    """
    value = chart_value_for(setting)
    assert value, f"the chart renders no value for {setting}, so there is nothing for an operator to set"

    supplied = rendered(**{value: UNMISTAKABLE})

    assert supplied[setting]["value"] == UNMISTAKABLE, (
        f"the chart renders {setting} without reading {value}, so an operator who changed this limit is "
        f"quietly ignored; it rendered {supplied[setting]['value']!r}"
    )


def test_the_values_file_states_every_one_of_them() -> None:
    """The default lives in the values file rather than only in the template.

    A rate the template reads and the values file never sets renders empty on
    a release that overrides nothing, which is the failure above arriving by a
    different door.
    """
    values = shipped(Path("infra/helm/inventory-tng/values.yaml"))

    missing = sorted(
        setting for setting in declared_rates() if f"{chart_value_for(setting).split('.')[-1]}:" not in values
    )

    assert not missing, f"{missing} are read by the chart and set nowhere in values.yaml, so they render empty"
