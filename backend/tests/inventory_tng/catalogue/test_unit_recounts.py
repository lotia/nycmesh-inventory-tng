"""The guided recount's own rules (catalogue/unit_recounts.py), where its
page can't reach them: what it plans to bring to zero at a place, and a
recount of an item retired after its page was read."""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session
from structlog.testing import capture_logs

from inventory_tng.catalogue import unit_recounts
from inventory_tng.core.db_errors import RuleBroken
from inventory_tng.stock import open_packages
from inventory_tng.stock.ledger import Entry, Form
from inventory_tng.web.forms import RETIRED

from helpers.sign_in import new_admin_id
from helpers.stock import Spools, received, recount, retire


def test_forms_that_cancel_once_loose_hold_nothing(session: Session) -> None:
    """A spool of 1 ft opened in ft, the cable set to m, the 0.305 m on it
    cut and the spool closed, then the cable in ft again: its form reads
    -0.001 ft, held loose, where 0.001 ft was received loose. The two
    cancel, so the store holds only its sealed spools."""
    spools = Spools(session, cable_unit="ft")
    cable, store = spools.cable, spools.store
    spools.spool.size = Decimal(1)
    opened = open_packages.make(session, uuid.uuid4(), spools.spool.id, store.id)
    on_it = Form(open_package_id=opened.id)
    spools.write(
        {"kind": "open", "from_location_id": store.id, "to_location_id": store.id},
        Entry(cable.id, Decimal(1), spools.sealed, on_it),
    )
    cable.unit = "m"
    session.flush()
    spools.write(
        {"kind": "install", "from_location_id": store.id},
        Entry(cable.id, Decimal("0.305"), on_it),
    )
    admin_id = new_admin_id(session)
    assert open_packages.close(
        session, opened.id, admin_id, datetime.now(UTC), how="direct"
    )
    cable.unit = "ft"
    session.flush()
    received(
        session,
        spools.place,
        [{"item_id": cable.id, "quantity": "0.001", "unit": "ft"}],
    )
    (held,) = unit_recounts.planned(session, cable).held
    assert held.forms == {spools.sealed: Decimal(609)}


def test_an_item_retired_since_its_page_is_refused(session: Session) -> None:
    spools = Spools(session)
    seen = unit_recounts.planned(session, spools.cable).seen
    retire(session, spools.cable)
    with capture_logs() as logs, pytest.raises(RuleBroken, match=RETIRED):
        recount(session, spools.cable, seen=seen)
    refused = [each for each in logs if each["event"] == "unit recount refused"]
    assert [each["reason"] for each in refused] == ["retired"]
