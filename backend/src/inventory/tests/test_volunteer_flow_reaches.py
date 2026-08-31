"""Every endpoint the volunteer screens call is one an open deployment answers.

THIS TEST EXISTS BECAUSE REASONING ABOUT IT FAILED. `inventory-tng-gnhl`
described the surface to open as "the catalogue and its balances, the
pick-list, the label map the client prefetches, and the resolver for one
scanned code", and that sentence was implemented by reading it rather than by
reading the client. Three of the four were wrong in some way:

- `LabelSheetView` was opened. It lays out stickers for a printer and only
  `admin/PrintLabels.tsx` asks for it, so that was a surface opened to
  strangers for no one's benefit.
- `LabelListView` was NOT opened, and it is the map `scan/labelCache.ts`
  actually prefetches. "The label map" named two plausible endpoints and the
  wrong one was picked.
- `ItemDetailView` was NOT opened, and `scan/applyCode.ts` falls back to it
  whenever the map has no name for a code -- a sticker minted since the last
  prefetch.
- `CategoryListView` was opened and nothing outside the admin screens fetches
  it; an item carries its category as a bare id.

Nothing in the suite noticed. Every test passed, the audit in
test_capabilities.py was satisfied because each opened view was argued, and a
demonstration would have failed at the first scan of an unfamiliar sticker.

So the client is asked instead. It is the only party that knows.
"""

import re
from pathlib import Path

import pytest
from django.conf import settings as configured
from django.urls import Resolver404, resolve
from pytest_django.fixtures import Settings

from inventory.tests import helpers
from inventory_tng import access

#: Where the browser application lives. Resolved against the repository root
#: rather than the working directory, as `helpers.shipped` does and for the
#: same reason: pytest runs from `backend/`.
FRONTEND = Path("frontend/src")

#: The screens an administrator signs in for. Excluded because they are
#: allowed to need endpoints a stranger may not have -- that is the whole of
#: decision 0012's split -- so including them would demand the catalogue be
#: writable by anybody.
ADMINISTRATORS = "admin"

#: A path this application serves, as the client spells it in a string or a
#: template literal.
#:
#: DELIBERATELY PERMISSIVE about what may appear inside it. The first version
#: of this listed the characters it expected and quietly dropped
#: `/api/labels/${encodeURIComponent(code)}` because of the brackets -- a
#: silent miss, in the file whose entire subject is a silent miss. Anything
#: that is not a quote, whitespace or the start of a query string is part of
#: the path, and `interpolated` below fails rather than skips when it cannot
#: make sense of one.
CALLED = re.compile(r"""["'`](/api/[^"'`?\s]*)""")

#: What an interpolated segment stands for, by the collection it hangs off.
#: Any value resolves the same route; the label code is the one that must not
#: be a number, because its converter is `str` and a `int` route would take
#: precedence for a digit.
SPECIMEN = {"labels": "0000000000"}
#: For everything else, whose converter is `int`.
ANY = "1"


def interpolated(path: str) -> str:
    """One client path with every `${...}` replaced by a value of the right shape.

    REFUSED RATHER THAN SKIPPED when a segment is interpolated and this cannot
    say what belongs there, for the reason `helpers.routes` gives about its own
    converters: a path quietly left out is a path nobody checks, and the whole
    of this file is about one of those.
    """
    segments = path.split("/")
    for index, segment in enumerate(segments):
        if "${" not in segment:
            continue
        collection = segments[index - 1] if index else ""
        segments[index] = SPECIMEN.get(collection, ANY)
    filled = "/".join(segments)
    assert "${" not in filled, f"{path} interpolates inside a segment and this cannot build a URL for it"
    return filled


def called_paths() -> set[str]:
    """Every `/api/...` the volunteer-facing client asks for.

    READ FROM THE SOURCE rather than listed here, because a list here is a
    second copy of the client's requirements and would go stale exactly as
    silently as the reasoning this test replaces.
    """
    found = set()
    root = configured.REPO_ROOT / FRONTEND
    for source in root.rglob("*.ts*"):
        relative = source.relative_to(root)
        if relative.parts[0] == ADMINISTRATORS or ".test." in source.name or "testFixtures" in source.name:
            continue
        for path in CALLED.findall(source.read_text()):
            filled = interpolated(path)
            if filled.rstrip("/") != "/api":
                found.add(filled)
    return found


def test_the_client_asks_for_something_at_all() -> None:
    """The walk above returning nothing would make every test below vacuous.

    A rename of `frontend/src`, a change of quoting style, a build step that
    moves these strings -- any of them empties the set, and an empty set
    satisfies every assertion about it.
    """
    found = called_paths()

    assert len(found) >= 5, f"only {sorted(found)} were found in {FRONTEND}, which is too few to be the whole client"


@pytest.mark.parametrize("path", sorted(called_paths()))
def test_every_path_the_client_calls_is_a_route_this_application_serves(path: str) -> None:
    """Ahead of the question below, because an unroutable path answers it wrongly.

    A typo in a client path would otherwise make the admission test pass by
    never finding a view to ask about.
    """
    # Asserted rather than `pytest.fail`, which this repository's type checker
    # reads as taking a bool.
    served = True
    try:
        resolve(path)
    except Resolver404:
        served = False

    assert served, f"the client calls {path} and this application serves no such route"


@pytest.mark.parametrize("path", sorted(called_paths()))
def test_an_open_deployment_answers_every_one_of_them(path: str, settings: Settings) -> None:
    """The assertion the whole file is for.

    Asked of ADMISSION rather than by driving a request, for the reason
    `helpers.admits_anonymously` gives: a real request needs a body, a
    fixture, and a status code to interpret, and what is being asked here is
    only whether the permission layer lets a stranger through.

    GET ONLY. The two writes a volunteer makes are `POST`s and are held by
    test_volunteer_access.py; what fails here is a READ the scan needs and
    cannot have, which is the shape the gap took.
    """
    settings.VOLUNTEER_ACCESS = access.OPEN

    route = next(candidate for candidate in helpers.routes() if resolve(path).func == candidate.callback)

    assert route.view is not None, f"{path} resolves to something with no view class to ask"
    assert helpers.admits_anonymously(route, "GET"), (
        f"the volunteer screens call {path} and an open deployment still refuses a caller with no "
        f"account, so the flow inventory-tng-gnhl opened stops here. Give {route.view.__name__} "
        "VOLUNTEER_READ, or stop the client asking for it"
    )
