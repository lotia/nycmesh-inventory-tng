"""Whose address a forwarded header is allowed to speak for.

Held on the function's own terms, the way `test_hosts.py` holds `allowed_hosts`
and for the same reason: this is a rule about strings, and reaching it through
an endpoint would be a slow way to learn that a loop runs the wrong way round.

Two of these are measurements rather than preferences. The counting reading
this replaces was forgeable with one trailing comma, and the pair below records
what it did, so that a change putting a count back is refused by a test naming
the thing it costs rather than by somebody remembering the argument.
"""

import re

import pytest
from django.conf import settings

from inventory_tng.forwarded import client_address, networks, trusted

# The chain a deployed request crosses: the ingress, then the frontend's nginx.
# `OURS` is written from these rather than beside them, because an `OURS` that
# had drifted from `NGINX` would turn every trusted-peer test below into an
# untrusted-peer one, and those still pass. A block and a bare address
# together, because that is the shape a cluster actually has -- see
# docs/deployment.md.
NGINX = "10.43.7.9"
INGRESS = "10.42.0.1"
CALLER = "203.0.113.7"
OUTSIDE = "198.51.100.4"
OURS = networks(["10.42.0.0/16", NGINX])


def request(remote_addr: str, forwarded: str | None = None) -> dict[str, str]:
    """A `request.META`, with only the two keys this reads."""
    meta = {"REMOTE_ADDR": remote_addr}
    if forwarded is not None:
        meta["HTTP_X_FORWARDED_FOR"] = forwarded
    return meta


# --------------------------------------------------------------------------
# The reading
# --------------------------------------------------------------------------


def test_the_caller_is_the_first_entry_this_deployment_did_not_append() -> None:
    """The whole chain, as a deployed request arrives with it.

    nginx is the peer, the ingress is what nginx appended, and the entry before
    that is what the ingress saw -- which is the caller.
    """
    meta = request(NGINX, f"{CALLER}, {INGRESS}")

    assert client_address(meta, OURS) == CALLER


def test_a_prefix_the_caller_wrote_is_never_reached() -> None:
    """A proxy appends AFTER what it received, so the caller's own address is
    always to the right of anything the caller invented.
    """
    meta = request(NGINX, f"9.9.9.9, evil, {CALLER}, {INGRESS}")

    assert client_address(meta, OURS) == CALLER


@pytest.mark.parametrize(
    "forwarded",
    [
        # A header shaped like the real chain, from somebody who is not on it.
        f"10.69.0.1, {INGRESS}",
        # MEASURED against the counting reading with `NUM_PROXIES=2`: this was
        # answered `10.69.0.1`, straight from the caller's keyboard.
        "10.69.0.1, 9.9.9.9",
        # MEASURED: the entry-counting guard decision 0023 sets out was
        # satisfied by this -- one comma makes two entries -- and answered
        # `10.69.0.1` as well.
        "10.69.0.1,",
        # Nothing to read, which is a development machine.
        None,
    ],
)
def test_a_header_from_an_address_we_do_not_believe_is_not_read_at_all(forwarded: str | None) -> None:
    """The case a hop count gets wrong in the dangerous direction.

    A request that reaches this application without crossing the proxies --
    misrouted ingress, a probe, anything else on the pod network -- claims
    whatever it likes and is answered with its own address. Parametrised
    because these differ only in what the caller wrote, and the whole point is
    that none of it is read: a header cleverer than the last is still a header
    from somebody this deployment does not believe.
    """
    assert client_address(request(OUTSIDE, forwarded), OURS) == OUTSIDE


def test_a_deployment_that_trusts_nobody_believes_no_header() -> None:
    """The shipped default, and what makes it safe rather than merely current:
    it can under-attribute and never over-attribute.
    """
    meta = request(NGINX, f"{CALLER}, {INGRESS}")

    assert client_address(meta, []) == NGINX


def test_a_request_with_no_header_is_its_own_peer() -> None:
    """Which is every request on a development machine."""
    assert client_address(request("127.0.0.1"), OURS) == "127.0.0.1"


def test_a_header_holding_only_our_own_proxies_falls_back_to_the_peer() -> None:
    """Nothing in it was written by a caller, so there is no caller in it."""
    meta = request(NGINX, f"{INGRESS}, {NGINX}")

    assert client_address(meta, OURS) == NGINX


def test_an_entry_that_is_not_an_address_is_somebody_talking() -> None:
    """A header entry is a string a caller may have invented, so anything that
    does not parse is certainly not one of this deployment's proxies -- and
    stopping there is right, because it is to the right of nothing.
    """
    meta = request(NGINX, f"not-an-address, {INGRESS}")

    assert client_address(meta, OURS) == "not-an-address"


def test_the_spaces_and_the_empty_entries_are_not_part_of_an_address() -> None:
    """A header is written by several programs and one of them adds spaces."""
    meta = request(NGINX, f"  {CALLER} , , {INGRESS} ,")

    assert client_address(meta, OURS) == CALLER


# --------------------------------------------------------------------------
# What the counting reading did, held so that a change back is noticed
# --------------------------------------------------------------------------


def test_a_trailing_comma_buys_nothing_from_inside_the_chain_either() -> None:
    """The two measurements above are of a header nothing reads.

    This is the same trailing comma arriving through the real chain, where a
    counting rule would still have had something to count -- and where the walk
    has to skip the empty entry the comma makes rather than stop on it.
    """
    assert client_address(request(NGINX, f"10.69.0.1,, {CALLER}, {INGRESS}"), OURS) == CALLER


# --------------------------------------------------------------------------
# The list itself
# --------------------------------------------------------------------------


def test_a_bare_address_is_the_block_holding_only_itself() -> None:
    assert trusted("10.43.7.9", networks(["10.43.7.9"]))
    assert not trusted("10.43.7.10", networks(["10.43.7.9"]))


def test_a_block_is_accepted_because_a_pod_address_is_assigned() -> None:
    assert trusted("10.42.9.201", OURS)
    assert not trusted("10.44.0.1", OURS)


def test_ipv6_is_read_as_well() -> None:
    """Nothing here is IPv4-only, and a cluster's pod range may not be."""
    ours = networks(["fd00::/8"])

    assert trusted("fd00::17", ours)
    assert not trusted("2001:db8::1", ours)


def test_an_entry_nobody_can_read_names_itself_and_the_setting() -> None:
    """Parsed at settings import, so this stops a pod rather than a request --
    and `TRUSTED_PROXIES: '10.42.0.0/16'` on its own does not say which file to
    open.
    """
    with pytest.raises(ValueError, match=re.escape("TRUSTED_PROXIES: 'not a network'")):
        networks(["not a network"])


def test_the_shipped_deployments_believe_nobody() -> None:
    """Empty in `.env.sample`, so a deployment that has not thought about this
    cannot be lied to. Read through the settings module rather than the file,
    so it is the value a process would actually run with.

    The chart's half of that claim is in `test_chart.py`, which asks the
    rendered manifest rather than `values.yaml`. A default read off the values
    file says nothing about whether a pod is ever handed it, and which of those
    two is worth asserting is that file's argument to make.
    """
    assert settings.TRUSTED_PROXIES == []
    assert "TRUSTED_PROXIES=\n" in (settings.REPO_ROOT / ".env.sample").read_text()
