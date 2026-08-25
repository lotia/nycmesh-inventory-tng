"""Whose address a forwarded header is allowed to speak for.

`X-Forwarded-For` is written by whoever calls and appended to by whatever the
request crosses, and the header does not say which entry is which. So reading
it safely is not a parsing problem: it is a question about the deployment, and
the answer is a list of addresses this deployment will believe a header FROM.
`TRUSTED_PROXIES` is that list, `.env.sample` is where a deployer is told which
way it is dangerous to get wrong, and
docs/decisions/0023-which-addresses-may-speak-for-another.md is the argument.

WHY NOT COUNT HOPS. Counting is what DRF's throttles do -- `NUM_PROXIES`
entries back from the right -- and no count can work, because entries are free.
Measured against `NUM_PROXIES=2`, a caller writing the header in full is handed
back as the caller:

    X-Forwarded-For: 10.69.0.1, 9.9.9.9   -> 10.69.0.1

and a rule that refuses a header with too few entries buys one comma of delay:

    X-Forwarded-For: 10.69.0.1,           -> 10.69.0.1

`test_forwarded.py` pins both, so whoever moves this line is told. The reading
below counts nothing. It walks the header from the right, discarding entries
this deployment put there itself, and stops at the first it did not -- which is
at worst the real caller, because every proxy appends AFTER whatever it
received. There is no arrangement of entries a caller can write that reaches
past their own.

WHAT NOTHING HERE CAN DO is help a deployment that trusts the wrong thing. The
list is the deployment's own proxies and nothing else, and getting it too wide
is the failure -- `.env.sample` is where that is put to whoever sets it, and it
is put there once rather than restated on every reader of the value.

NOTHING IN THIS APPLICATION CALLS THIS YET, and that is deliberate rather than
an oversight. Nothing here decides who gets in on an address, because a session
is asked of everything; `inventory-tng-gnhl` is the first issue that would, and
this is its precondition. The throttles deliberately keep DRF's reading, and
decision 0023 says what that costs and why it is the right trade for a rate
limit.
"""

from collections.abc import Mapping, Sequence
from ipaddress import IPv4Network, IPv6Network, ip_address, ip_network

Network = IPv4Network | IPv6Network

SETTING = "TRUSTED_PROXIES"


def networks(listed: Sequence[str]) -> list[Network]:
    """The configured list, as things an address can be tested against.

    An entry is an address or a CIDR block, and a bare address is the block
    holding only itself. Both are accepted because a cluster's ingress rarely
    has an address anybody can write down in advance -- what is knowable is the
    range it is drawn from.

    Parsed once, at settings import, so that a value nobody can read stops the
    process at boot rather than at the first request that needed it. The
    refusal names `SETTING` and the entry, because "does not appear to be an
    IPv4 or IPv6 network" on its own does not say which file to open. Named as
    a constant rather than taken as an argument: there is one setting, and a
    parameter for a second would be generality kept for nothing.

    The list is already trimmed of the spaces a person writes after a comma;
    `environment.entries` is where that happens and why.
    """
    parsed = []
    for entry in listed:
        try:
            parsed.append(ip_network(entry, strict=False))
        except ValueError as complaint:
            raise ValueError(f"{SETTING}: {entry!r} is not an address or a CIDR block. {complaint}") from None
    return parsed


def trusted(candidate: str, proxies: Sequence[Network]) -> bool:
    """Whether this deployment believes a forwarded header from that address.

    False for anything that is not an address at all, which is the case that
    matters: an entry in the header is a string a caller may have invented, and
    a string that does not parse is certainly not one of this deployment's own
    proxies.

    NO PORT IS STRIPPED, deliberately. `10.42.0.1:34567` and `[2001:db8::1]:443`
    are both refused here, and for an entry a caller wrote that is the answer
    wanted. For one of OUR proxies it would not be -- the walk would stop at
    the proxy and hand its `address:port` back as the caller -- but nothing
    this repository deploys writes one: ingress-nginx and the chart's own nginx
    both append a bare address. And a string carrying a port does not parse for
    whoever reads the answer either, so it is refused there rather than
    mistaken for a caller. Stripping one would mean deciding where an IPv6
    address ends, which is a parser this has no reason to own.
    """
    try:
        address = ip_address(candidate.strip())
    except ValueError:
        return False
    return any(address in proxy for proxy in proxies)


def client_address(meta: Mapping[str, str], proxies: Sequence[Network]) -> str:
    """The caller's address, as far as this deployment can honestly tell.

    `meta` is a request's `META`, which Django's request and DRF's both carry.
    `proxies` is `settings.TRUSTED_PROXIES`; it is passed rather than read here
    so that this module stays a function of its arguments and a test does not
    have to arrange a settings override to ask it something.

    The peer is checked first and the header is not read at all unless the peer
    is one of ours. A request that reaches this application without crossing a
    proxy -- a misrouted ingress, a probe, anything else on the pod network --
    therefore gets its own address rather than whatever it claimed, which is
    the case a hop count gets wrong in the dangerous direction.
    """
    peer = meta.get("REMOTE_ADDR", "").strip()
    if not trusted(peer, proxies):
        return peer

    # Split, trimmed and blanks dropped, which is what `environment.entries`
    # does -- and deliberately not that function. This module imports nothing
    # but the standard library, because it is on a request path and `entries`
    # sits beside `django-environ` where the settings are read. The two are one
    # comprehension apiece and are allowed to stay separate.
    for entry in reversed((meta.get("HTTP_X_FORWARDED_FOR") or "").split(",")):
        candidate = entry.strip()
        if candidate and not trusted(candidate, proxies):
            return candidate
    # Every entry was one of ours, or there were none: the peer is the nearest
    # thing to a caller this request has.
    return peer
