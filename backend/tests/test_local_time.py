"""Times are UTC; one site time zone shows them (docs/decisions/0013)."""

from argparse import Namespace
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session
from structlog.testing import capture_logs

from inventory_tng import local_time, registration_codes
from inventory_tng.local_time import (
    DEFAULT_ZONE,
    is_time_zone,
    local,
    show,
    site_zone,
)
from inventory_tng.models import SiteSettings

from helpers.rows import history, violates


def test_times_come_back_in_utc_whatever_the_servers_zone(engine: Engine) -> None:
    with engine.connect() as connection:
        assert connection.scalar(text("SHOW TimeZone")) == "UTC"
        moment = connection.scalar(text("SELECT now()"))
    assert moment is not None
    assert moment.utcoffset() == timedelta(0)


def test_the_site_zone_defaults_to_new_york(session: Session) -> None:
    assert session.get_one(SiteSettings, 1).time_zone == DEFAULT_ZONE
    assert site_zone(session).key == "America/New_York"


@pytest.mark.parametrize(
    ("utc", "shown"),
    [
        # Daylight saving ends at 2:00 EDT on 1 November 2026...
        (datetime(2026, 11, 1, 5, 59, tzinfo=UTC), "2026-11-01 01:59 EDT"),
        # ...so the clock shows 1:00 again, an hour later, in EST.
        (datetime(2026, 11, 1, 6, 0, tzinfo=UTC), "2026-11-01 01:00 EST"),
        # And it starts again at 2:00 EST on 8 March 2026.
        (datetime(2026, 3, 8, 6, 59, tzinfo=UTC), "2026-03-08 01:59 EST"),
        (datetime(2026, 3, 8, 7, 0, tzinfo=UTC), "2026-03-08 03:00 EDT"),
    ],
)
def test_shown_times_follow_daylight_saving(
    session: Session, utc: datetime, shown: str
) -> None:
    assert show(utc, site_zone(session)) == shown


def test_another_zone_can_be_chosen(session: Session) -> None:
    session.get_one(SiteSettings, 1).time_zone = "Europe/London"
    session.flush()
    zone = site_zone(session)
    assert show(datetime(2026, 7, 1, 12, tzinfo=UTC), zone) == "2026-07-01 13:00 BST"


def test_a_time_without_a_zone_is_refused(session: Session) -> None:
    with pytest.raises(ValueError, match="store UTC"):
        local(datetime(2026, 7, 1, 12), site_zone(session))


@pytest.mark.parametrize("name", ["Mars/Olympus", "../../etc/passwd", "", "+05:00"])
def test_the_database_refuses_an_unknown_zone(session: Session, name: str) -> None:
    assert not is_time_zone(name)
    with violates(session, "site_settings_time_zone"):
        session.get_one(SiteSettings, 1).time_zone = name


def test_the_settings_row_is_one_never_deleted_and_history(session: Session) -> None:
    with violates(session, "ck_site_settings_one_row"):
        session.add(SiteSettings(id=2))
    with violates(session, "site_settings_kept"):
        session.delete(session.get_one(SiteSettings, 1))
    session.get_one(SiteSettings, 1).time_zone = "America/Chicago"
    session.flush()
    change = history(session, "site_settings")[-1]
    assert change.new_row is not None
    assert change.new_row["time_zone"] == "America/Chicago"


def test_the_command_line_shows_local_times(
    session: Session, capsys: pytest.CaptureFixture[str]
) -> None:
    args = Namespace(command="create", uses=1, days=1.0, label=None, base_url=None)
    registration_codes.run(session, args)
    first = capsys.readouterr().out.splitlines()[0]
    assert first.endswith(("EST", "EDT"))


def test_a_zone_these_rules_do_not_know_falls_back(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PostgreSQL's list of zones and tzdata's can differ."""
    session.get_one(SiteSettings, 1).time_zone = "America/Chicago"
    session.flush()
    real = local_time.ZoneInfo

    def unknown(name: str) -> ZoneInfo:
        if name == "America/Chicago":
            raise ZoneInfoNotFoundError(name)
        return real(name)

    monkeypatch.setattr(local_time, "ZoneInfo", unknown)
    with capture_logs() as logs:
        assert site_zone(session).key == DEFAULT_ZONE
    assert any("unknown here" in line["event"] for line in logs)
