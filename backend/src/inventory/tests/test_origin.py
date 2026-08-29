"""The host Django is told it has, and the origin the browser claims.

Nothing in the application chooses that host: the reverse proxy in front of it
does. So the comparison spans two files, and the reasoning behind which nginx
variable carries it lives once, beside the configuration, on the ``map`` at the
top of ``frontend/nginx.conf.template``. Read that first — this module only
holds it to it.

Both halves are here rather than apart, because either alone passes while the
system is broken. A test of Django's comparison passes whatever nginx forwards;
a test of nginx's configuration passes whatever Django then does with it.
"""

import re
from typing import Any

from django.conf import settings
from django.contrib.auth.models import User
from django.middleware.csrf import get_token
from django.test import Client, RequestFactory, override_settings
from django.urls import reverse

NGINX_TEMPLATE = "frontend/nginx.conf.template"
FORWARDS_THE_HOST = re.compile(r"proxy_set_header\s+Host\s+(\$\w+);")
# The two arms of the `map` that variable comes from, asserted apart rather
# than as one block. Either deleted on its own is still a failure, which is the
# whole point of checking them; matching them together would have added nothing
# but a dependence on the order they happen to be written in, and a map's arms
# do not depend on it while they do not overlap.
MAPS_AN_ABSENT_HOST = re.compile(r'""\s+\$host;')
MAPS_EVERY_OTHER_HOST = re.compile(r"default\s+\$http_host;")

# A browser on a port that is not the scheme's default, which is the only
# condition under which any of this goes wrong.
BROWSER_HOST = "localhost:8080"
BROWSER_ORIGIN = f"http://{BROWSER_HOST}"
WITHOUT_THE_PORT = BROWSER_HOST.partition(":")[0]

# What Django says when the comparison fails. Matched on rather than the status
# alone, because a 403 is also what a signed-out session, a throttle and a
# missing token produce, and this would pass on any of them.
ORIGIN_REFUSED = "Origin checking failed"


def test_nginx_forwards_the_browsers_host_rather_than_a_host_without_its_port() -> None:
    """Hold every proxied location to the host the browser actually used.

    This is the defect itself, and the template's `map` is where the reason
    lives. Every location is held to the same variable rather than only the
    ones whose upstream is Django: only Django compares origins, so only Django
    can show this fault, but a proxy that rewrites the host for part of what
    sits behind it and not the rest has to be reasoned about twice. Uniformity
    is cheaper to keep than an exception list, and nothing here wants the
    exception.
    """
    template = (settings.REPO_ROOT / NGINX_TEMPLATE).read_text()
    forwarded = FORWARDS_THE_HOST.findall(template)

    assert forwarded, (
        f"{NGINX_TEMPLATE} no longer sets a Host header on anything it proxies, so Django is left reading "
        "the address of whatever nginx happened to dial, and the comparison below is against a host that no "
        "browser sent"
    )
    assert set(forwarded) == {"$forwarded_host"}, (
        f"{NGINX_TEMPLATE} forwards the host as {sorted(set(forwarded))}. The map above says what the "
        f"normalised one costs; the symptom is {ORIGIN_REFUSED!r} on every write, and only on a stack "
        "reached by a port"
    )
    assert MAPS_EVERY_OTHER_HOST.search(template), (
        f"{NGINX_TEMPLATE} no longer maps an ordinary request to the browser's own header, so the port is "
        "dropped again and every write is refused"
    )
    assert MAPS_AN_ABSENT_HOST.search(template), (
        f"{NGINX_TEMPLATE} no longer maps a request carrying no Host at all onto one, so nginx forwards an "
        "empty header where it used to substitute its own, and Django answers DisallowedHost"
    )


def signed_in() -> tuple[Client, str]:
    """A session that checks CSRF the way a browser makes Django check it.

    ``enforce_csrf_checks`` is the point: the ordinary test client exempts
    itself, so every other write test in this suite passes with the defect in
    place. Signed in for a duller reason -- DRF exempts the view itself and
    re-enforces CSRF inside its session authentication, which does not run at
    all until somebody is signed in, so an anonymous request never reaches the
    comparison under test.

    The token is minted directly rather than read back from a response cookie.
    ``CSRF_COOKIE_SECURE`` is on whenever ``DEBUG`` is off, so a cookie set over
    plain HTTP never reaches the jar, and reading one back would make this test
    pass or fail on which settings the suite happened to run under. It carries
    no host of its own: what Django compares is the host on the request being
    written, which ``write`` below is handed.
    """
    client = Client(enforce_csrf_checks=True)
    client.force_login(User.objects.create_user(username="scanner", password="not-a-real-password"))
    token = get_token(RequestFactory().get("/"))
    client.cookies[settings.CSRF_COOKIE_NAME] = token
    return client, token


def write(client: Client, token: str, host: str) -> Any:
    """Attempt one write.

    The body is deliberately empty. What is asserted is whether the request
    survived the comparison, which happens before any serializer sees it, so a
    refusal about the body means it got through.
    """
    return client.post(
        reverse("stock-transactions"),
        data="{}",
        content_type="application/json",
        HTTP_HOST=host,
        HTTP_ORIGIN=BROWSER_ORIGIN,
        HTTP_X_CSRFTOKEN=token,
    )


# Both hosts here are this one with and without a port, and Django matches this
# list with the port already stripped. Pinned on each test rather than
# inherited, because the suite's own ALLOWED_HOSTS varies by environment:
# without it CI answers DisallowedHost to both requests, and an assertion that
# some refusal did NOT happen passes against the wrong refusal.
ADMITS_THE_HOST = override_settings(ALLOWED_HOSTS=[WITHOUT_THE_PORT])


@ADMITS_THE_HOST
def test_a_write_is_accepted_when_the_host_carries_the_port_the_browser_used(db: None) -> None:
    """What ``$http_host`` produces.

    Not a claim that the write succeeds -- the body is empty and a serializer
    will refuse it -- but that it is refused for what it contains rather than
    for where it came from. The content type is asserted too: Django's own
    refusals here are HTML, so a page saying anything else would satisfy the
    first assertion while proving nothing.
    """
    client, token = signed_in()

    response = write(client, token, BROWSER_HOST)

    assert ORIGIN_REFUSED not in response.content.decode(), (
        "a signed-in browser reaching this application by a port cannot write at all, which is the whole of "
        "decision 0011's scanning flow and every administrative screen"
    )
    assert response["Content-Type"].startswith("application/json"), (
        "the write never reached a serializer, so whatever refused it was not the empty body this test "
        "sends and the assertion above is vacuous"
    )


@ADMITS_THE_HOST
def test_a_write_is_refused_when_the_port_is_dropped_from_the_host(db: None) -> None:
    """What the proxy produced before the fix, kept as a specimen.

    Asserted so that the test above cannot quietly stop meaning anything: were
    Django to stop comparing origins at all, that one would still pass and this
    one would not.
    """
    client, token = signed_in()

    assert ORIGIN_REFUSED in write(client, token, WITHOUT_THE_PORT).content.decode(), (
        "Django no longer refuses a write whose Origin disagrees with the host it was handed, so nothing "
        "here constrains what the proxy forwards"
    )
