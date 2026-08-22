"""`inventory_tng.hosts.allowed_hosts`, on its own terms.

Here rather than in `test_chart.py` because it is a pure function and reaching
it through a `helm` subprocess and a database-backed client is a slow way to
learn that a list comprehension is wrong. `test_chart.py` does the thing only
it can do -- hold the rendered manifest against the running application -- and
these hold the parsing rules that neither `helm` nor Django owns.

The whitespace case is the one that matters: it is a live production bug in
this repository's history, invisible in a values file, and the reason this
function exists at all rather than the list being used where it is read.
"""

from inventory_tng.hosts import allowed_hosts


def test_the_space_after_a_comma_is_not_part_of_the_hostname() -> None:
    """`django-environ` splits and does nothing else, so `"a, b"` yields `" b"`.

    Which matches no request Django ever receives, so the host is refused for
    ever and the space does not show in the file that caused it.
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
