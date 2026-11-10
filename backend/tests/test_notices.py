"""A notice after a change is said by the heading of the section it is
about, which the redirect scrolls to (pages.redirect's `section`); one about
the whole page, under its title."""

import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from inventory_tng import pages
from inventory_tng.models import Admin, Category, Item, Label, Location, Volunteer

from helpers.devices import new_device
from helpers.rows import add, tag
from helpers.sign_in import text

SOURCE = Path(pages.__file__).parent
TEMPLATES = SOURCE / "templates"

pytestmark = pytest.mark.usefixtures("admin_client")


def said_in(page: str) -> str:
    """Where the page says its notice: the id of the section it sits in, or
    "top" under the title. The page says it once."""
    (status,) = re.finditer(r'<p role="status">', page)
    opened = [
        match
        for match in re.finditer(r"<section([^>]*)>", page)
        if match.start() < status.start()
    ]
    closed = page.rfind("</section>", 0, status.start())
    if not opened or closed > opened[-1].start():
        return "top"
    named = re.search(r'\bid="([a-z-]+)"', opened[-1].group(1))
    return named.group(1) if named else "unnamed"


@pytest.mark.parametrize(
    ("path", "notice", "section"),
    [
        ("/admin/admins", "admin_retired", "retired"),
        ("/admin/items", "retired", "retired"),
        ("/admin/categories", "retired", "retired"),
        ("/admin/locations", "retired", "retired"),
        ("/admin/volunteers", "retired", "retired"),
        ("/admin/settings", "saved", "sign-in"),
        ("/admin/settings", "site_saved", "time-zone"),
        ("/admin/settings", "settings_saved", "scan-page"),
        ("/admin/settings", "added", "reasons"),
        ("/admin/counts", "counted", "count"),
        ("/admin/counts", "adjusted", "adjust"),
        ("/admin/labels", "too_many", "current"),
        ("/admin/labels", "label_revoked", "inactive"),
        ("/admin/account", "email", "email"),
        ("/admin/account", "removed", "authenticators"),
        ("/admin/account", "others", "sessions"),
    ],
)
@pytest.mark.usefixtures("one_of_each_retired")
def test_a_notice_is_said_by_its_sections_heading(
    admin_client: TestClient, path: str, notice: str, section: str
) -> None:
    page = text(admin_client.get(f"{path}?notice={notice}&section={section}"))
    assert pages.NOTICES[notice] in page
    assert said_in(page) == section


@pytest.fixture
def one_of_each_retired(session: Session) -> None:
    """As a retire leaves, so each page has its Retired section."""
    now = datetime.now(UTC)
    category = add(session, Category(name=f"Old {tag()}", retired_at=now))
    item = add(session, Item(name=f"Old {tag()}", category_id=category.id))
    item.retired_at = now
    add(session, Location(name=f"Old {tag()}", kind="store", retired_at=now))
    add(session, Volunteer(name=f"Old {tag()}", retired_at=now))
    add(session, Admin(username=f"old{tag()}", retired_at=now))
    add(session, Label(item_id=item.id, revoked_at=now))
    session.flush()


def test_a_codes_device_revoked_is_said_by_its_devices(
    admin_client: TestClient, session: Session
) -> None:
    device, _ = new_device(session)
    path = f"/admin/devices/codes/{device.registration_code_id}"
    page = text(admin_client.get(f"{path}?notice=device_revoked&section=devices"))
    assert said_in(page) == "devices"


def test_a_notice_about_the_whole_page_is_said_under_its_title(
    admin_client: TestClient,
) -> None:
    page = text(admin_client.get("/admin/categories?notice=added"))
    assert said_in(page) == "top"


def test_a_section_the_page_lacks_says_nothing_but_breaks_nothing(
    admin_client: TestClient,
) -> None:
    page = admin_client.get("/admin/settings?notice=saved&section=no-such-place")
    assert page.status_code == 200
    assert '<p role="status">' not in page.text


def test_the_redirect_goes_to_the_section() -> None:
    went = pages.redirect("/admin/settings", notice="saved", section="sign-in")
    assert went.headers["location"] == (
        "/admin/settings?notice=saved&section=sign-in#sign-in"
    )
    with pytest.raises(ValueError, match="no notice for section"):
        pages.redirect("/admin/settings", section="sign-in")
    with pytest.raises(ValueError, match="no notice for section"):
        pages.redirect("/admin/settings", notice="saved", section="Sign In")


def test_every_section_a_redirect_names_is_on_a_page() -> None:
    """So no notice is sent where no page says it."""
    named = set()
    for module in SOURCE.rglob("*.py"):
        source = module.read_text()
        named |= set(re.findall(r'section="([a-z-]+)"', source))
    shown = set()
    for template in TEMPLATES.glob("*.html"):
        shown |= set(re.findall(r'with section_id="([a-z-]+)"', template.read_text()))
    assert named
    assert named <= shown
