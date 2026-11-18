"""A notice after a change is said by the heading of the section it is
about, which the redirect scrolls to (pages.redirect's `section`); one about
the whole page, under its title."""

import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from inventory_tng.models.admins import Admin
from inventory_tng.models.catalogue import Category, Item
from inventory_tng.models.labels import Label
from inventory_tng.models.places import Location
from inventory_tng.models.volunteers import Volunteer
from inventory_tng.volunteers.routes import BRING_THEM_BACK
from inventory_tng.web import pages

from helpers.markup import read, text
from helpers.rows import add, tag

TEMPLATES = Path(pages.__file__).parent.parent / "templates"

pytestmark = pytest.mark.usefixtures("admin_client")


def said_in(page: str) -> str:
    """Where the page says its notice: the id of the section it sits in, or
    "top" under the title. The page says it once."""
    read_page = read(page)
    assert len(read_page.all("p", role="status")) == 1
    # Outer sections come before those inside them: the last is the nearest.
    around = [s for s in read_page.all("section") if s.all("p", role="status")]
    if not around:
        return "top"
    return around[-1].attrs.get("id") or "unnamed"


@pytest.mark.parametrize(
    ("path", "notice", "section"),
    [
        ("/admin/admins", "admin_retired", "retired"),
        ("/admin/settings", "saved", "sign-in"),
        ("/admin/settings", "site_saved", "time-zone"),
        ("/admin/settings", "settings_saved", "scan-page"),
        ("/admin/settings", "added", "reasons"),
        ("/admin/counts", "counted", "count"),
        ("/admin/counts", "adjusted", "adjust"),
        ("/admin/account", "email", "email"),
        ("/admin/account", "removed", "authenticators"),
        ("/admin/account", "others", "sessions"),
    ],
)
@pytest.mark.usefixtures("one_of_each_retired")
def test_a_notice_is_said_by_its_sections_heading(
    admin_client: TestClient, path: str, notice: str, section: str
) -> None:
    response = admin_client.get(f"{path}?notice={notice}&section={section}")
    assert str(pages.NOTICES[notice]) in text(response)
    assert said_in(response.text) == section


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


def test_a_notice_about_the_whole_page_is_said_under_its_title(
    admin_client: TestClient,
) -> None:
    page = admin_client.get("/admin/categories?notice=added").text
    assert said_in(page) == "top"


def test_a_section_no_page_has_says_the_notice_under_the_title(
    admin_client: TestClient,
) -> None:
    """A link with any other section, made by hand, loses no notice."""
    page = admin_client.get("/admin/settings?notice=saved&section=no-such-place").text
    assert said_in(page) == "top"


def test_the_redirect_goes_to_the_section() -> None:
    went = pages.redirect("/admin/settings", notice="saved", section="sign-in")
    assert went.headers["location"] == (
        "/admin/settings?notice=saved&section=sign-in#sign-in"
    )
    with pytest.raises(ValueError, match="no notice for section"):
        pages.redirect("/admin/settings", section="sign-in")
    with pytest.raises(ValueError, match="no notice for section"):
        pages.redirect("/admin/settings", notice="saved", section="Sign In")


def test_a_notice_key_another_module_added_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Else whichever module was imported last would speak for both pages
    (inventory-tng-80j6); nothing of a refused call is added."""
    added: dict[str, str] = {}
    monkeypatch.setattr(pages, "_NOTICES", added)
    pages.add_notices({"saved": "Saved."})
    with pytest.raises(ValueError, match="notice already added: saved"):
        pages.add_notices({"saved": "Kept.", "never_added": "Never."})
    assert added == {"saved": "Saved."}


def test_notices_are_added_only_through_add_notices() -> None:
    """So no module can skip its check."""
    with pytest.raises(TypeError):
        pages.NOTICES["saved"] = "Kept."  # ty: ignore[invalid-assignment]


def test_the_notice_sections_are_the_sections_pages_say_notices_by() -> None:
    """Both ways: no notice is sent where no page says it, and no page
    waits for a section no redirect may name."""
    shown = set()
    for template in TEMPLATES.glob("*.html"):
        shown |= set(re.findall(r'with section_id="([a-z-]+)"', template.read_text()))
    assert shown == pages.NOTICE_SECTIONS


@pytest.mark.parametrize(
    ("path", "row", "words"),
    [
        ("/admin/items", "item", "Bring it back"),
        ("/admin/categories", "category", "Bring it back"),
        ("/admin/locations", "location", "Bring it back"),
        ("/admin/volunteers", "volunteer", BRING_THEM_BACK),
    ],
)
def test_a_retire_is_undone_from_its_notice(
    admin_client: TestClient, session: Session, path: str, row: str, words: object
) -> None:
    """One tap retires, and its notice offers a button to bring it back,
    while it is retired (docs/decisions/0019)."""
    category = add(session, Category(name=f"Spare {tag()}"))
    made = {
        "item": lambda: Item(name=f"Spare {tag()}", category_id=category.id),
        "category": lambda: Category(name=f"Spare {tag()}"),
        "location": lambda: Location(name=f"Spare {tag()}", kind="store"),
        "volunteer": lambda: Volunteer(name=f"Spare {tag()}"),
    }[row]
    retiring = add(session, made())
    retired = admin_client.post(f"{path}/{retiring.id}/retire")
    assert retired.status_code == 303
    landed = retired.headers["location"]
    assert landed == f"{path}?undo={retiring.id}&notice=retired"
    undo = read(admin_client.get(landed).text).one(
        "form", action=f"{path}/{retiring.id}/restore"
    )
    assert undo.attrs["method"] == "post"
    assert [(b.text, b.classes) for b in undo.all("button")] == [(words, [])]
    assert admin_client.post(f"{path}/{retiring.id}/restore").status_code == 303
    # Back already: the notice says what was done, and offers nothing more.
    page = admin_client.get(landed).text
    assert read(page).all("p", "Retired.", role="status")
    assert "/restore" not in page


@pytest.mark.parametrize("undo", ["", "nonsense", "999999"])
def test_an_undo_for_nothing_retired_offers_nothing(
    admin_client: TestClient, session: Session, undo: str
) -> None:
    current = add(session, Category(name=f"Current {tag()}"))
    for asked in (undo, str(current.id)):
        page = text(admin_client.get(f"/admin/categories?notice=retired&undo={asked}"))
        assert "/restore" not in page
