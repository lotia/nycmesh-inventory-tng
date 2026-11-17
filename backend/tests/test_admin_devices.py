"""Registration codes and devices from the browser (admin/devices.py)."""

import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from babel.messages.catalog import Catalog
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from inventory_tng import devices
from inventory_tng.admin.common import NAME_MAX, up_to
from inventory_tng.admin.devices import (
    AFTER_FROM,
    BULK_REVOKE,
    CODES,
    DEVICE_LIST,
    DEVICES,
    days_problem,
    revoke_many_title,
    uses_problem,
)
from inventory_tng.models import Device, RegistrationCode, SiteSettings
from inventory_tng.phrases import compiled, speaking

from helpers.devices import new_device
from helpers.markup import (
    Element,
    button,
    forms,
    hidden_fields,
    links,
    options,
    read,
    row_links,
)
from helpers.rows import history
from helpers.sign_in import confirmed_long_ago, once, text


def make_code(client: TestClient, **form: str) -> tuple[Response, str]:
    made = client.post(CODES, data=once(uses="2", days="1", label="Open day") | form)
    match = re.search(r'class="key">([a-z-]+)<', made.text)
    return made, match.group(1) if match else ""


def test_making_a_code_shows_it_once(
    admin_client: TestClient, session: Session
) -> None:
    made, phrase = make_code(admin_client)
    assert made.status_code == 200
    assert made.headers["cache-control"] == "no-store"
    page = text(made)
    assert "Shown only now." in page
    assert read(made.text).all("svg")
    assert f"/register?code={phrase}" in page
    # Ready to print: one sheet, with where to type the phrase.
    assert page.count('class="print-sheet"') == 1
    assert page.count("://testserver/register</strong>") == 1
    code = session.scalars(select(RegistrationCode)).one()
    assert (code.label, code.max_uses) == ("Open day", 2)
    assert (history(session, "registration_code", code.id)[0].actor or "").startswith(
        "admin:"
    )
    # Listed afterwards, without the phrase.
    listed = text(admin_client.get(DEVICES))
    assert "Open day" in listed
    assert phrase not in listed


def test_a_code_made_here_registers_a_phone(
    admin_client: TestClient,
    session: Session,
    make_client: Callable[..., TestClient],
) -> None:
    _, phrase = make_code(admin_client)
    phone = make_client(admin_client.app, address="192.0.2.50")
    registered = phone.post("/api/devices", json={"code": phrase})
    assert registered.status_code == 201
    code = session.scalars(select(RegistrationCode)).one()
    page = text(admin_client.get(f"{CODES}/{code.id}"))
    assert "1 of 2 used" in page
    assert "192.0.2.50" in page


def test_limits_on_a_code(admin_client: TestClient) -> None:
    for form, problem in (
        ({"uses": "0"}, uses_problem(500)),
        ({"uses": "many"}, uses_problem(500)),
        ({"days": "91"}, days_problem(90)),
        ({"label": "x" * (NAME_MAX + 1)}, up_to(NAME_MAX)),
    ):
        refused, _ = make_code(admin_client, **form)
        assert refused.status_code == 400
        assert str(problem) in text(refused)


def test_revoking_a_code_and_a_device(
    admin_client: TestClient, session: Session
) -> None:
    _, phrase = make_code(admin_client)
    device, _ = devices.register(session, phrase, "192.0.2.51")
    code = session.scalars(select(RegistrationCode)).one()
    done = admin_client.post(f"{CODES}/{code.id}/revoke")
    assert done.headers["location"] == f"{CODES}/{code.id}?notice=code_revoked"
    assert code.revoked_at is not None
    assert device.revoked_at is None  # Its devices stay registered.
    done = admin_client.post(f"{DEVICES}/{device.id}/revoke")
    assert done.headers["location"] == f"{DEVICES}/{device.id}?notice=device_revoked"
    session.refresh(device)
    assert device.revoked_at is not None


def test_a_code_is_revoked_after_a_confirmation(
    admin_client: TestClient, session: Session
) -> None:
    make_code(admin_client)
    code = session.scalars(select(RegistrationCode)).one()
    path = f"{CODES}/{code.id}"
    assert ("get", f"{path}/revoke") in forms(admin_client.get(path).text)
    asked = admin_client.get(f"{path}/revoke")
    assert asked.status_code == 200
    words = " ".join(text(asked).split())
    assert "Once revoked, Open day registers no more devices" in words
    assert "danger" in button(asked.text, "Revoke").classes
    assert ("Keep it", path) in links(asked.text)
    assert code.revoked_at is None  # Only its button revokes.
    admin_client.post(f"{path}/revoke")
    assert admin_client.get(f"{path}/revoke").status_code == 404
    assert admin_client.get(f"{CODES}/999999/revoke").status_code == 404


def test_changes_here_ask_for_a_fresh_code(
    admin_client: TestClient, session: Session
) -> None:
    confirmed_long_ago(session)
    asked, _ = make_code(admin_client)
    assert asked.status_code == 403
    assert "needs a fresh" in text(asked)
    assert session.scalars(select(RegistrationCode)).first() is None


def test_unknown_codes_and_devices_are_not_found(admin_client: TestClient) -> None:
    assert admin_client.get(f"{CODES}/999999").status_code == 404
    assert admin_client.post(f"{CODES}/999999/revoke").status_code == 404
    assert admin_client.post(f"{DEVICES}/999999/revoke").status_code == 404


# Every device, filtered, and revoking many at once (ph9)


def device_at(session: Session, at: datetime) -> Device:
    """A registered device, as if registered `at`."""
    device, _ = new_device(session)
    device.created_at = at
    session.flush()
    return device


def test_devices_are_listed_by_when_they_registered(
    admin_client: TestClient, session: Session
) -> None:
    """Since is typed in the site's time zone (America/New_York here)."""
    old = device_at(session, datetime(2026, 11, 1, 12, tzinfo=UTC))
    new = device_at(session, datetime(2026, 11, 5, 12, tzinfo=UTC))
    listed = text(admin_client.get(DEVICE_LIST, params={"since": "2026-11-03T00:00"}))
    assert f"Device {new.id}" in listed
    assert f"Device {old.id}" not in listed
    by_code = admin_client.get(
        DEVICE_LIST, params={"code": str(old.registration_code_id)}
    )
    assert f"Device {old.id}" in text(by_code)
    assert f"Device {new.id}" not in text(by_code)
    bad = admin_client.get(DEVICE_LIST, params={"since": "soon", "code": "x"})
    assert bad.status_code == 400
    assert "A date and time" in text(bad)
    assert "A code&#39;s number" in bad.text


def test_revoking_the_ticked_ones_asks_first(
    admin_client: TestClient, session: Session
) -> None:
    """With how many, then only those ticked that the filter still matches."""
    a = device_at(session, datetime(2026, 11, 5, 12, tzinfo=UTC))
    b = device_at(session, datetime(2026, 11, 5, 13, tzinfo=UTC))
    kept = device_at(session, datetime(2026, 11, 5, 14, tzinfo=UTC))
    old = device_at(session, datetime(2026, 11, 1, 12, tzinfo=UTC))
    form = once(since="2026-11-03T00:00", code="", state="current")
    ticked = form | {"device": [str(a.id), str(b.id), str(old.id)]}
    asked = admin_client.post(BULK_REVOKE, data=ticked)
    assert revoke_many_title(2) in text(asked)
    assert a.revoked_at is None
    done = admin_client.post(BULK_REVOKE, data=ticked | {"sure": "yes"})
    assert done.status_code == 303
    assert "notice=devices_revoked" in done.headers["location"]
    assert a.revoked_at is not None
    assert b.revoked_at is not None
    assert kept.revoked_at is None
    # Out of the filter: not revoked, though ticked.
    assert old.revoked_at is None
    # Sent again: those ticked are revoked already, so nothing is done again.
    again = admin_client.post(BULK_REVOKE, data=ticked | {"sure": "yes"})
    assert again.headers["location"].startswith(DEVICE_LIST)
    nothing = admin_client.post(BULK_REVOKE, data=form)
    assert nothing.headers["location"].startswith(DEVICE_LIST)


def test_one_device_is_asked_about_in_the_singular(
    admin_client: TestClient, session: Session
) -> None:
    a = device_at(session, datetime(2026, 11, 5, 12, tzinfo=UTC))
    asked = admin_client.post(BULK_REVOKE, data=once(device=str(a.id)))
    assert revoke_many_title(1) in text(asked)


def test_a_date_past_what_a_time_holds_is_refused(admin_client: TestClient) -> None:
    refused = admin_client.get(DEVICE_LIST, params={"since": "9999-12-31T23:59"})
    assert refused.status_code == 400


def test_the_devices_page_links_to_every_device(admin_client: TestClient) -> None:
    assert f'href="{DEVICE_LIST}"' in admin_client.get(DEVICES).text


def test_revoked_and_all_devices_are_listed_too(
    admin_client: TestClient, session: Session
) -> None:
    current = device_at(session, datetime(2026, 11, 5, 12, tzinfo=UTC))
    gone = device_at(session, datetime(2026, 11, 5, 13, tzinfo=UTC))
    devices.revoke_device(session, gone.id)
    # Revoking it again finds no current device.
    assert not devices.revoke_device(session, gone.id)
    revoked = text(admin_client.get(DEVICE_LIST, params={"state": "revoked"}))
    assert f"Device {gone.id}" in revoked
    assert f"Device {current.id}" not in revoked
    every = text(admin_client.get(DEVICE_LIST, params={"state": "all"}))
    assert f"Device {gone.id}" in every
    assert f"Device {current.id}" in every
    # Nothing current among those ticked: nothing revoked, nothing counted.
    none = devices.revoke_devices(
        session, devices.DeviceFilter(), [gone.id], datetime.now(UTC)
    )
    assert none == 0


def test_a_bulk_revoke_is_sent_once(admin_client: TestClient, session: Session) -> None:
    """Its key spent, the same form names a device still current: refused."""
    a = device_at(session, datetime(2026, 11, 5, 12, tzinfo=UTC))
    b = device_at(session, datetime(2026, 11, 5, 13, tzinfo=UTC))
    form = once(sure="yes")
    assert (
        admin_client.post(BULK_REVOKE, data=form | {"device": str(a.id)}).status_code
        == 303
    )
    again = admin_client.post(BULK_REVOKE, data=form | {"device": str(b.id)})
    assert again.status_code == 409
    assert b.revoked_at is None


def test_each_device_has_a_page_reached_from_both_lists(
    admin_client: TestClient, session: Session
) -> None:
    device, _ = new_device(session)
    link = (device.name, f"{DEVICES}/{device.id}")
    assert link in links(admin_client.get(DEVICE_LIST).text)
    assert link in links(
        admin_client.get(f"{CODES}/{device.registration_code_id}").text
    )
    page = text(admin_client.get(f"{DEVICES}/{device.id}"))
    assert f'href="{CODES}/{device.registration_code_id}"' in page
    assert "Revoke this device" in page
    assert admin_client.get(f"{DEVICES}/999999").status_code == 404


def test_naming_a_device(admin_client: TestClient, session: Session) -> None:
    device, _ = new_device(session)
    path = f"{DEVICES}/{device.id}"
    version = str(device.edit_version)
    done = admin_client.post(path, data={"label": " Store tablet ", "version": version})
    assert done.headers["location"] == f"{path}?notice=saved"
    session.refresh(device)
    assert device.label == "Store tablet"
    # A form shown before that save is refused, not undoing it.
    stale = admin_client.post(path, data={"label": "Old", "version": version})
    assert stale.status_code == 409
    long = admin_client.post(
        path, data={"label": "x" * (NAME_MAX + 1), "version": str(device.edit_version)}
    )
    assert long.status_code == 400
    # Blank, it goes by its number again.
    admin_client.post(path, data={"label": "", "version": str(device.edit_version)})
    session.refresh(device)
    assert device.label is None
    assert (
        admin_client.post(f"{DEVICES}/999999", data={"label": "x"}).status_code == 404
    )


def test_a_device_is_revoked_after_a_confirmation(
    admin_client: TestClient, session: Session
) -> None:
    device, _ = new_device(session)
    path = f"{DEVICES}/{device.id}"
    asked = read(admin_client.get(f"{path}/revoke").text)
    assert "danger" in button(asked, "Revoke").classes
    assert ("Keep it", path) in links(asked)
    assert device.revoked_at is None
    admin_client.post(f"{path}/revoke")
    session.refresh(device)
    assert device.revoked_at is not None
    # Revoked: read only, and not asked about again.
    page = admin_client.get(path).text
    assert read(page).all("span", "Revoked", class_="tag")
    assert "Revoke this device" not in page
    assert not read(page).all("button", "Save")
    assert admin_client.get(f"{path}/revoke").status_code == 404
    refused = admin_client.post(
        path, data={"label": "Lost", "version": str(device.edit_version)}
    )
    assert refused.status_code == 409


def listed_ids(page: str | Element) -> list[int]:
    """The devices a list shows, in its order."""
    return [
        int(found[1])
        for _, href in links(page)
        if (found := re.fullmatch(rf"{re.escape(DEVICES)}/(\d+)", href))
    ]


def test_devices_are_listed_between_two_times(
    admin_client: TestClient, session: Session
) -> None:
    """In the site's zone (America/New_York, UTC-5 in November); the end
    takes in the whole minute it names."""
    before = device_at(session, datetime(2026, 11, 5, 13, 59, tzinfo=UTC))
    first = device_at(session, datetime(2026, 11, 5, 14, 0, tzinfo=UTC))
    last = device_at(session, datetime(2026, 11, 5, 15, 30, 59, tzinfo=UTC))
    after = device_at(session, datetime(2026, 11, 5, 15, 31, tzinfo=UTC))
    window = {"since": "2026-11-05T09:00", "until": "2026-11-05T10:30"}
    listed = admin_client.get(DEVICE_LIST, params=window)
    page = text(listed)
    shown = listed_ids(listed.text)
    assert first.id in shown
    assert last.id in shown
    assert before.id not in shown
    assert after.id not in shown
    assert f"{len(shown)} listed" in page
    backwards = {"since": "2026-11-05T10:30", "until": "2026-11-05T09:00"}
    refused = admin_client.get(DEVICE_LIST, params=backwards)
    assert refused.status_code == 400
    assert str(AFTER_FROM) in text(refused)
    bad = admin_client.get(DEVICE_LIST, params={"until": "later"})
    assert bad.status_code == 400


def test_devices_are_sorted_by_registered_or_last_used(
    admin_client: TestClient, session: Session
) -> None:
    early = device_at(session, datetime(2026, 11, 5, 12, tzinfo=UTC))
    late = device_at(session, datetime(2026, 11, 5, 13, tzinfo=UTC))
    early.last_seen_at = datetime(2026, 11, 6, 12, tzinfo=UTC)
    late.last_seen_at = datetime(2026, 11, 5, 13, tzinfo=UTC)
    session.flush()
    window = {"since": "2026-11-05T07:00", "until": "2026-11-05T08:00"}

    def order(sort: str, way: str = "") -> list[int]:
        asked = window | {"sort": sort, "order": way}
        page = admin_client.get(DEVICE_LIST, params=asked).text
        return [i for i in listed_ids(page) if i in (early.id, late.id)]

    assert order("") == [late.id, early.id]
    assert order("registered") == [late.id, early.id]
    assert order("registered", "asc") == [early.id, late.id]
    assert order("used") == [early.id, late.id]
    assert order("used", "asc") == [late.id, early.id]
    assert order("nonsense", "sideways") == [late.id, early.id]
    # The headings sort, the filter kept, and say how it is sorted now:
    # newest registered first, so pressed again, oldest first.
    page = admin_client.get(DEVICE_LIST, params=window).text
    assert 'aria-sort="descending"' in page
    assert "sort=registered&amp;order=asc" in page
    assert 'sort=used"' in page
    assert "until=2026-11-05T08%3A00" in page


def test_devices_come_in_pages_of_the_size_asked(
    admin_client: TestClient, session: Session
) -> None:
    """Pages of 25, 50, 100 or All, not a cap (Ali, 2026-11-15,
    inventory-tng-qwb); the filter kept, and cleared by Clear."""
    start = datetime(2026, 11, 7, 12, tzinfo=UTC)
    made = [device_at(session, start + timedelta(minutes=n)) for n in range(26)]
    window = {"since": "2026-11-07T07:00", "until": "2026-11-07T08:00"}
    listed = admin_client.get(DEVICE_LIST, params=window)
    assert len(listed_ids(listed.text)) == 26
    assert "26 listed." in text(listed)
    listed = admin_client.get(DEVICE_LIST, params=window | {"per": "25"})
    page = text(listed)
    assert listed_ids(listed.text) == [d.id for d in reversed(made)][:25]
    assert "26 listed." in page
    assert "Page 1 of 2" in page
    # Revoke selected reaches this page's alone, and says so.
    assert "Every current one on this page is selected" in page
    shown = read(admin_client.get(DEVICE_LIST, params=window | {"per": "25"}).text)
    (offer,) = [
        p
        for p in shown.all("p")
        if p.text.endswith("Show All to revoke those on the other pages too.")
    ]
    show_all = offer.one("a", "Show All").attrs["href"]
    assert re.fullmatch(rf"{DEVICE_LIST}\?.*per=all", show_all)
    second = read(
        admin_client.get(DEVICE_LIST, params=window | {"per": "25", "page": "2"}).text
    )
    assert listed_ids(second) == [made[0].id]
    # Revoking from a page keeps its page and size.
    assert ("per", "25") in hidden_fields(second)
    assert ("page", "2") in hidden_fields(second)
    every = admin_client.get(DEVICE_LIST, params=window | {"per": "all"}).text
    assert len(listed_ids(every)) == 26
    assert "Every current one listed is selected" in every
    assert "other pages" not in every
    # Clear drops the filter, not the size.
    assert ("Clear", f"{DEVICE_LIST}?per=all") in links(every)


def test_a_revoke_keeps_the_window_and_sort(
    admin_client: TestClient, session: Session
) -> None:
    device = device_at(session, datetime(2026, 11, 5, 12, tzinfo=UTC))
    form = once(
        since="2026-11-05T06:00",
        until="2026-11-05T08:00",
        code="",
        state="current",
        sort="used",
    )
    asked = admin_client.post(BULK_REVOKE, data=form | {"device": str(device.id)})
    assert 'name="until" value="2026-11-05T08:00"' in asked.text
    assert 'name="sort" value="used"' in asked.text
    done = admin_client.post(
        BULK_REVOKE, data=form | {"device": str(device.id), "sure": "yes"}
    )
    assert "until=2026-11-05T08%3A00" in done.headers["location"]
    assert "sort=used" in done.headers["location"]
    new_device(session)
    page = text(admin_client.get(DEVICE_LIST))
    assert "Every current one listed is selected: clear any to keep." in page
    assert "Revoke selected" in page


def test_an_end_at_the_last_minute_there_is_lists_without_an_end(
    admin_client: TestClient, session: Session
) -> None:
    """In UTC, the last minute a time holds has no minute after it."""
    session.get_one(SiteSettings, 1).time_zone = "UTC"
    session.flush()
    device = device_at(session, datetime(2026, 11, 5, 12, tzinfo=UTC))
    listed = admin_client.get(DEVICE_LIST, params={"until": "9999-12-31T23:59"})
    assert listed.status_code == 200
    assert device.id in listed_ids(listed.text)


def test_codes_sort_filter_by_state_and_come_in_pages(
    admin_client: TestClient, session: Session
) -> None:
    """Newest first unless a heading sorts them; filtered by state (open,
    used up, expired, revoked: the words the table shows); in pages of 25,
    50, 100 or all (Ali, 2026-11-15, inventory-tng-qwb)."""
    at = datetime.now(UTC)
    made: dict[str, RegistrationCode] = {}
    for label, uses, days, revoked in (
        ("Bravo", 0, 3, False),
        ("Alpha", 2, 2, False),
        ("Charlie", 0, -1, False),
        ("Delta", 0, 1, True),
    ):
        code, _phrase = devices.create_code(
            session, max_uses=2, lifetime=timedelta(days=1), label=label
        )
        code.uses = uses
        code.expires_at = at + timedelta(days=days)
        code.created_at = at - timedelta(days=len(made))
        code.revoked_at = at if revoked else None
        made[label] = code
    session.commit()

    def listed(**query: str) -> list[str]:
        page = admin_client.get(DEVICES, params=query).text
        return row_links(page, CODES)

    assert listed() == ["Bravo", "Alpha", "Charlie", "Delta"]
    assert listed(sort="code") == ["Alpha", "Bravo", "Charlie", "Delta"]
    assert listed(sort="code", order="desc") == ["Delta", "Charlie", "Bravo", "Alpha"]
    assert listed(sort="state") == ["Charlie", "Bravo", "Delta", "Alpha"]
    # By the state's words in the language spoken, not English.
    catalogue = Catalog()
    catalogue.add("Expired", "Verfallen")
    with speaking(compiled(catalogue)):
        assert listed(sort="state") == ["Bravo", "Delta", "Alpha", "Charlie"]
    assert listed(sort="used", order="desc")[0] == "Alpha"
    assert listed(sort="until") == ["Charlie", "Delta", "Alpha", "Bravo"]
    assert listed(state="open") == ["Bravo"]
    assert listed(state="used up") == ["Alpha"]
    assert listed(state="expired") == ["Charlie"]
    assert listed(state="revoked") == ["Delta"]
    assert listed(state="lost") == listed()
    assert listed(state="usable") == listed()
    page = read(admin_client.get(DEVICES, params={"state": "expired"}).text)
    assert ("open", "Open", False) in options(page)
    assert ("expired", "Expired", True) in options(page)
    assert ("Clear", DEVICES) in links(page)
    assert ("Until", f"{DEVICES}?state=expired&sort=until") in links(page)
    for number in range(26):
        devices.create_code(
            session, max_uses=1, lifetime=timedelta(days=1), label=f"More {number}"
        )
    session.commit()
    paged = admin_client.get(DEVICES, params={"per": "25"}).text
    assert len(row_links(paged, CODES)) == 25
    assert "Page 1 of 2" in paged
    assert len(listed(per="all")) == 30
