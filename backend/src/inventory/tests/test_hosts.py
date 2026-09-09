"""`inventory_tng.hosts.allowed_hosts`, on its own terms.

Here rather than in `test_chart.py` because it is a pure function and reaching
it through a `helm` subprocess and a database-backed client is a slow way to
learn that a list comprehension is wrong. `test_chart.py` does the thing only
it can do -- hold the rendered manifest against the running application -- and
these hold the parsing rules that neither `helm` nor Django owns.

The whitespace case is the one that matters: it is a live production bug in
this repository's history, invisible in a values file, and the reason this
function exists at all rather than the list being used where it is read.

The last two tests are not about the function. They are here because this is
where the topic lives, and because the defect they hold off was not a parsing
rule at all -- it was a value that never reached the parsing. See
`inventory-tng-w5r7`.
"""

from pathlib import Path

from inventory.tests.helpers import shipped
from inventory_tng.hosts import allowed_hosts

#: The file the two compose tests below read.
COMPOSE = Path("compose.yaml")


def test_the_space_after_a_comma_is_not_part_of_the_hostname() -> None:
    """That this list is trimmed too, whoever supplied it.

    Why a comma-separated variable needs trimming at all is argued once, on
    `inventory_tng.environment.entries`, and held in `test_environment.py`.
    What is asserted here is the other caller: a list handed straight to this
    function, which is what `test_chart.py` does with what the chart renders.
    """
    assert allowed_hosts(["first.example.org", " second.example.org"], []) == [
        "first.example.org",
        "second.example.org",
    ]


def test_an_address_the_deployment_supplies_is_added_to_the_list() -> None:
    """Added, never substituted: a browser's hostname has to keep working."""
    assert allowed_hosts(["inventory.nycmesh.net"], ["10.42.0.17"]) == [
        "inventory.nycmesh.net",
        "10.42.0.17",
    ]


def test_the_supplied_address_is_stripped_too() -> None:
    """Two of them arrive comma-separated from one downward API field."""
    assert allowed_hosts([], ["10.42.0.17", " fd00::17"]) == ["10.42.0.17", "fd00::17"]


def test_nothing_blank_is_ever_allowed() -> None:
    """An empty pattern would match nothing, but it is worth being certain.

    Everywhere but a cluster there is no address to supply, and a value of `""`
    reaches here as one blank element rather than as no elements.
    """
    assert allowed_hosts(["", "  ", "inventory.nycmesh.net"], ["", " "]) == ["inventory.nycmesh.net"]


def test_compose_reads_the_allowed_hosts_variable_rather_than_carrying_a_literal() -> None:
    """The one arrangement where setting this used to do nothing.

    `compose.yaml` named three hosts outright, so the value a reader put in
    `.env` was overridden by the file and a phone on the LAN was answered with
    `400 DisallowedHost` -- before TLS, before the camera, before anything the
    scanning work is actually about. Every route to a device changes the Host
    Django sees, so this is refused first whichever one is taken.

    Asserted against the text rather than through `yaml.safe_load`, which is
    what the other shipped-configuration tests in this suite do: it is the
    interpolation itself that is under test, and parsing the file resolves it
    away.
    """
    assert "DJANGO_ALLOWED_HOSTS: ${DJANGO_ALLOWED_HOSTS:-localhost,127.0.0.1}" in shipped(COMPOSE), (
        "compose.yaml does not pass DJANGO_ALLOWED_HOSTS through with the loopback default, so a value in "
        ".env is overridden by the file and a LAN address cannot be added; testing from a phone is then "
        "refused with 400 DisallowedHost before TLS or the camera is reached at all"
    )


def test_compose_supplies_the_names_a_narrowed_list_would_otherwise_lose() -> None:
    """The half that is easy to lose, and it is why this is two tests.

    `backend` and the loopback names were in the literal, and the passthrough
    above would drop them out of any stack whose `.env` narrows the list. For
    `backend` that is silent, because nothing in this arrangement dials that
    name often enough to fail loudly. For `127.0.0.1` it is not silent but it
    is worse: the backend's own healthcheck dials it, so the service never
    reaches health and the `seed` service waiting on it never runs.

    Neither is an address a developer supplies, so both belong in the variable
    this repository keeps for what an arrangement supplies for itself -- added
    to the list rather than replacing it, and itself passed through so that
    setting it in `.env` is not a third dead knob. `allowed_hosts` above is
    where the argument for the two kinds lives.
    """
    supplied = "DJANGO_EXTRA_ALLOWED_HOSTS: backend,localhost,127.0.0.1,${DJANGO_EXTRA_ALLOWED_HOSTS:-}"

    assert supplied in shipped(COMPOSE), (
        "compose.yaml no longer admits the names this arrangement supplies for itself on top of whatever "
        ".env lists -- the backend service's name on the compose network, and the loopback address its own "
        "healthcheck dials. Without the second, an .env naming only a LAN address leaves this service "
        "permanently unhealthy and the seed service that waits on it never runs"
    )
