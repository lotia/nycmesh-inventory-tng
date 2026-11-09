"""How the scan page behaves, as administrators set it."""

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from structlog.testing import capture_logs

from inventory_tng import scan_settings
from inventory_tng.models import CatalogueHistory, ScanSettings

from helpers.rows import violates


def run(session: Session, *argv: str) -> int:
    return scan_settings.run(session, scan_settings.parse(argv))


def test_the_migration_makes_the_one_row_with_its_default(session: Session) -> None:
    assert session.scalars(select(ScanSettings)).one().name_check_minutes == 15


def test_set_changes_it_and_says_so(
    session: Session, capsys: pytest.CaptureFixture[str]
) -> None:
    with capture_logs() as logs:
        assert run(session, "set", "--name-check-minutes", "20") == 0
    assert session.get_one(ScanSettings, 1).name_check_minutes == 20
    assert capsys.readouterr().out == "name check: asks after 20 minutes unused\n"
    (line,) = [line for line in logs if line["event"] == "scan settings changed"]
    assert line["name_check_minutes"] == 20


def test_zero_never_asks(session: Session, capsys: pytest.CaptureFixture[str]) -> None:
    run(session, "set", "--name-check-minutes", "0")
    assert capsys.readouterr().out == "name check: never asks\n"


def test_a_change_is_history_with_who_made_it(session: Session) -> None:
    run(session, "set", "--name-check-minutes", "30")
    change = session.scalars(
        select(CatalogueHistory)
        .where(CatalogueHistory.table_name == "scan_settings")
        .order_by(CatalogueHistory.id.desc())
    ).first()
    assert change is not None
    assert change.actor == "system:cli"
    assert change.new_row is not None
    assert change.new_row["name_check_minutes"] == 30


@pytest.mark.parametrize(
    "argv",
    [
        ["set"],
        ["set", "--name-check-minutes", "-1"],
        ["set", "--name-check-minutes", "1441"],
        ["set", "--name-check-minutes", "²"],
    ],
)
def test_the_command_refuses_nothing_to_set_or_out_of_range(argv: list[str]) -> None:
    with pytest.raises(SystemExit):
        scan_settings.parse(argv)


@pytest.mark.parametrize("minutes", [-5, 24 * 60 + 1])
def test_the_database_keeps_one_row_in_range(session: Session, minutes: int) -> None:
    with violates(session, "ck_scan_settings_one_row"):
        session.add(ScanSettings(id=2))
    with violates(session, "ck_scan_settings_name_check_minutes"):
        session.get_one(ScanSettings, 1).name_check_minutes = minutes
