"""Whether nginx will notice an upstream that has moved.

What goes wrong when it does not, and why, is written down once beside the
`resolver` directive in ``frontend/nginx.conf.template``. In short: an upstream
that moves is never found again, and the symptom points at the wrong container.

Two things together undo it, and either alone is useless -- somewhere to ask,
and an upstream held in a variable so that nginx asks per request. Both are
held here because nothing else would notice one of them going: the stack serves
perfectly well right up until something restarts, which is not a thing CI does.
"""

import re
from pathlib import Path

from inventory.tests.helpers import NGINX_TEMPLATE, shipped

FRONTEND_DOCKERFILE = Path("frontend") / "Dockerfile"

# Somewhere to ask. The addresses themselves are not written down -- they
# differ by container runtime -- so what is asserted is that the entrypoint's
# variable is what fills this in.
HAS_A_RESOLVER = re.compile(r"resolver\s+\$\{NGINX_LOCAL_RESOLVERS\}")
# And the switch that makes the entrypoint publish it. Without this the
# variable renders empty, and `resolver ;` is a configuration nginx refuses to
# start on -- so a missing switch is a frontend that does not serve at all.
ASKS_FOR_RESOLVERS = re.compile(r"NGINX_ENTRYPOINT_LOCAL_RESOLVERS=1")

# Every upstream, and what it is dialled through.
PROXIES_TO = re.compile(r"proxy_pass\s+(\S+?);")
# An nginx variable is `$name`. `${NAME}` is envsubst's, and the difference is
# the whole point: envsubst replaces its form with a literal origin before
# nginx ever reads the file, so a `proxy_pass ${BACKEND_ORIGIN};` is resolved
# once at startup exactly like a hostname written out by hand. It only looks
# like a variable, which is why this is matched rather than a leading `$`.
AN_NGINX_VARIABLE = re.compile(r"^\$[A-Za-z_]")


def template() -> str:
    return shipped(NGINX_TEMPLATE)


def test_there_is_a_resolver_to_ask() -> None:
    """Half of it, and the half that has to come from the runtime."""
    assert HAS_A_RESOLVER.search(template()), (
        f"{NGINX_TEMPLATE} names no resolver, so nginx has nowhere to ask and keeps whatever address it "
        "resolved at startup; restarting the backend alone then answers 502 on every proxied path until the "
        "frontend is restarted too"
    )
    assert ASKS_FOR_RESOLVERS.search(shipped(FRONTEND_DOCKERFILE)), (
        f"{FRONTEND_DOCKERFILE} no longer asks the entrypoint to publish NGINX_LOCAL_RESOLVERS, so the directive "
        "above renders empty and nginx refuses to start at all"
    )


def test_every_upstream_is_dialled_through_a_variable() -> None:
    """The other half, and the one that is easy to undo by accident.

    A `proxy_pass` naming a host directly is resolved once whatever resolver is
    configured, so a single location written the plain way is that location
    holding a dead address after a restart while its neighbours recover. The
    symptom is worse than the original for being partial.
    """
    upstreams = PROXIES_TO.findall(template())

    assert upstreams, f"{NGINX_TEMPLATE} proxies nothing at all"
    remembered = [target for target in upstreams if not AN_NGINX_VARIABLE.match(target)]
    assert not remembered, (
        f"{NGINX_TEMPLATE} dials {remembered} without going through a variable, so nginx resolves those "
        "once at startup and never again; that location answers 502 after its upstream restarts while the "
        "others recover"
    )
