"""Print this machine's address on its local network, for `mise run
phone-lan` (CONTRIBUTING.md, On a phone):

    uv run python -m devtools lan-address

INVENTORY_LAN_ADDRESS, from the shell or .env, names one, as when the
machine has more than one network. Otherwise it is the address the
machine would send from to reach the internet: connecting a UDP socket
only picks the route, and sends nothing. An address reachable from the
internet is refused, so the app is never published there, and so is
IPv6, which compose and Caddy would need written in brackets.
"""

import argparse
import ipaddress
import socket
from collections.abc import Sequence

from inventory_tng.core.local_env import setting

KEY = "INVENTORY_LAN_ADDRESS"
# A documentation address (TEST-NET-1), reached by the default route, as
# the internet is: nothing is sent to it.
OUTSIDE = ("192.0.2.1", 9)


def routed_address() -> str:
    """The address this machine sends from on its way to the internet."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        try:
            probe.connect(OUTSIDE)
        except OSError as error:
            raise SystemExit(f"No network to find an address on; set {KEY}") from error
        return probe.getsockname()[0]


def lan_address() -> str:
    """The setting's address, else the routed one, so long as it is a
    local one."""
    address = setting(KEY) or routed_address()
    try:
        parsed = ipaddress.IPv4Address(address)
    except ValueError as error:
        raise SystemExit(f"{KEY} must be an IPv4 address, not {address!r}") from error
    if parsed.is_global or parsed.is_unspecified:
        raise SystemExit(
            f"{address} is not a local network's address: "
            f"set {KEY} to this machine's address on yours"
        )
    return address


def main(argv: Sequence[str], prog: str) -> int:
    parser = argparse.ArgumentParser(
        prog=prog,
        description="Print this machine's address on its local network.",
    )
    parser.parse_args(argv)
    print(lan_address())
    return 0
