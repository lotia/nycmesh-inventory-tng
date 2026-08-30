"""Whether anything is actually serving the admin's own stylesheets.

Django's admin is not decoration here. guides/administrator.md sends an
administrator into it for the work the application cannot do yet, and an admin
with no stylesheet has no navigation and no layout -- so an unstyled one is a
usability failure rather than a cosmetic complaint.

Held against settings and the Dockerfile rather than by fetching a URL, and the
reason is worth stating. What serves those files under gunicorn is a WSGI
middleware wrapping Django from outside, so Django's test client never reaches
it: a test asking the test client for a static path answers 404 whether or not
the arrangement is right. That is the trap `inventory-tng-iqff.1` recorded once
and the nb8 epic recorded again, and it is why the argument below is made in
two halves instead.
"""

import re

from django.conf import settings

from inventory.tests.helpers import BACKEND_DOCKERFILE, shipped

WHITENOISE = "whitenoise.middleware.WhiteNoiseMiddleware"

COLLECTS = re.compile(r"manage\.py\s+collectstatic")
# Anything that brings STATIC_ROOT into existence. Matched loosely on purpose:
# what matters is that the directory is there before the line below runs, not
# which command put it there.
MAKES_THE_DIRECTORY = re.compile(r"mkdir[^\n]*\bstaticfiles\b")


def test_whitenoise_is_loaded_exactly_where_there_are_collected_files_to_serve() -> None:
    """The invariant, in whatever environment this suite is run.

    Membership follows the directory and nothing else. This is what the fix
    changed: the condition used to be ``not DEBUG``, which stood in for "is
    something else already serving these" -- a question whose real answer is
    ``runserver``, not the debug flag. The arrangement that fell through the
    gap was DEBUG on AND gunicorn, which is what compose.yaml ships, and there
    every admin stylesheet answered 404.

    Asserting the biconditional rather than one side catches both regressions
    with one statement: loading it where nothing was collected warns on every
    start and serves nothing, and not loading it where files were collected is
    the defect itself. Under CI, where DEBUG is off and collectstatic has not
    run, restoring the old condition fails this outright.
    """
    assert (WHITENOISE in settings.MIDDLEWARE) == settings.STATIC_FILES_ARE_COLLECTED, (
        f"WhiteNoise is {'loaded' if WHITENOISE in settings.MIDDLEWARE else 'absent'} while "
        f"{settings.STATIC_ROOT} {'exists' if settings.STATIC_ROOT.is_dir() else 'does not exist'}. Those "
        "have to agree: under gunicorn nothing else serves collected files, and in a checkout there is "
        "nothing collected to serve"
    )


def test_the_image_makes_the_directory_before_it_collects_into_it() -> None:
    """The chicken and egg, asserted so that it stays got right.

    Why the order matters is the comment on STORAGES in settings.py, and is
    not repeated here. What belongs here is that NONE of the checks which
    already exist would have caught getting it wrong: the compose smoke test
    fetches static files directly, and unhashed copies are present and served;
    the backend suite runs in a checkout, where STATIC_ROOT is absent and the
    manifest backend is never chosen at all; the images job builds and stops.
    Measured rather than reasoned -- without the mkdir, collectstatic reports
    "164 copied", and with it "164 copied, 474 post-processed".

    So this asserts the ORDER, which is the whole of the fix.
    """
    dockerfile = shipped(BACKEND_DOCKERFILE)
    made = MAKES_THE_DIRECTORY.search(dockerfile)
    collects = COLLECTS.search(dockerfile)

    assert made, (
        f"{BACKEND_DOCKERFILE} no longer creates {'staticfiles'} before collecting into it, so the build "
        "writes no manifest and every page the image renders returns 500 -- while every test here passes"
    )
    assert collects and made.start() < collects.start(), (
        f"{BACKEND_DOCKERFILE} creates the static directory AFTER collectstatic rather than before, which "
        "is the same failure: the storage is chosen while the directory is still absent"
    )


def test_the_image_collects_the_files_the_rule_above_depends_on() -> None:
    """The other half, and without it the first proves nothing.

    The invariant above is satisfied just as well by an image that collects
    nothing -- no directory, no middleware, and an admin as unstyled as before.
    What closes that is the build actually producing the directory, which is
    the only reason the rule above resolves the way it does where it matters.
    """
    dockerfile = shipped(BACKEND_DOCKERFILE)

    assert COLLECTS.search(dockerfile), (
        f"{BACKEND_DOCKERFILE} no longer runs collectstatic, so STATIC_ROOT does not exist in the built image, so "
        "WhiteNoise is not loaded and the Django admin renders with no styling at all -- with the rule above "
        "still passing, because it agrees with itself about nothing being there"
    )
