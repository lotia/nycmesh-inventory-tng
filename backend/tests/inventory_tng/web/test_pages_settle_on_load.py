"""A page answering a form's POST, as a refusal or an error does, is marked
<html data-settle-on-load> by page() itself, so every such page, a new one
too, has its taps wait a moment as it loads: the second tap of the double
tap that sent the form lands on nothing (Ali, 2026-11-27, inventory-tng-
0c2z; docs/decisions/0019, Double taps). A page a link was followed to is
not marked. The redirect after a save is told by the browser
(frontend/src/admin/settle-on-load.ts)."""

from fastapi.testclient import TestClient
from httpx2 import Response

from inventory_tng.site_settings.paths import SETTINGS

from helpers.markup import read
from helpers.rows import tag


def settles(response: Response) -> bool:
    return "data-settle-on-load" in read(response.text).attrs


def test_a_page_a_link_was_followed_to_takes_taps_at_once(
    admin_client: TestClient,
) -> None:
    assert not settles(admin_client.get(SETTINGS))


def test_a_form_refused_is_sent_back_waiting(admin_client: TestClient) -> None:
    name = f"Found {tag()}"
    assert admin_client.post(f"{SETTINGS}/reasons", data={"name": name}).is_redirect
    refused = admin_client.post(f"{SETTINGS}/reasons", data={"name": name})
    assert refused.status_code == 400
    assert settles(refused)
    # What an ignored tap focuses, there being no notice.
    read(refused.text).one("h1", tabindex="-1")


def test_an_error_answering_a_form_waits_too(admin_client: TestClient) -> None:
    gone = admin_client.post(f"{SETTINGS}/reasons/999999", data={"action": "retire"})
    assert gone.status_code == 404
    assert settles(gone)
