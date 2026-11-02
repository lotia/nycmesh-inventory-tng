"""The settings page: how the scan page behaves (admin/settings.py)."""

import html

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from test_models import history

from inventory_tng.admin.settings import SETTINGS
from inventory_tng.models import ScanSettings


def test_shows_and_changes_the_name_check(
    admin_client: TestClient, session: Session
) -> None:
    assert 'value="15"' in admin_client.get(SETTINGS).text
    done = admin_client.post(f"{SETTINGS}/scan", data={"name_check_minutes": "30"})
    assert done.headers["location"] == f"{SETTINGS}?notice=settings_saved"
    assert session.get_one(ScanSettings, 1).name_check_minutes == 30
    change = history(session, "scan_settings")[-1]
    assert (change.actor or "").startswith("admin:")
    # The scan page reads it with the catalogue.
    assert "within ten minutes" in html.unescape(
        admin_client.get(done.headers["location"]).text
    )


@pytest.mark.parametrize("typed", ["", "-1", "soon", "1441"])
def test_refuses_what_is_not_minutes(admin_client: TestClient, typed: str) -> None:
    refused = admin_client.post(f"{SETTINGS}/scan", data={"name_check_minutes": typed})
    assert refused.status_code == 400
    assert "A whole number of minutes" in refused.text


def test_zero_never_asks(admin_client: TestClient, session: Session) -> None:
    admin_client.post(f"{SETTINGS}/scan", data={"name_check_minutes": "0"})
    assert session.get_one(ScanSettings, 1).name_check_minutes == 0
