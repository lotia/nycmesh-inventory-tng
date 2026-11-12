"""What the browser tests (frontend/browser-tests/) run against. Playwright's
webServer (playwright.config.ts) starts

    uv run python tests/browser_database.py serve

with BROWSER_RUN, a folder, and BROWSER_PORT set. It makes a database of
its own (databases.py), migrated and seeded, with a registration code for
the tests' device, and in BROWSER_RUN: prepared.json, what the tests need,
camera.y4m, the QR code of the seed's labelled item for Chromium to
show as its camera, and totp-keys, a key of the run's own to seal
authenticators with. It then serves the built frontend from that database
on BROWSER_PORT until Playwright stops it, and drops the database and its
files. A database a run leaves behind is dropped by a later run, as test
databases are.
"""

import argparse
import contextlib
import json
import os
import secrets
import signal
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import segno
from sqlalchemy import select
from sqlalchemy.orm import Session

from inventory_tng import __main__ as server
from inventory_tng import accounts, devices, seed, totp_keys, volunteers
from inventory_tng.models import Admin, AdminPassword, Item, Label, Volunteer

from databases import migrated_engine, new_database

# What a run writes in its folder: these, and the signed-in session
# sign-in.setup.ts keeps (frontend/browser-tests/prepared.ts, adminState).
# The other administrator's username.
OTHER_ADMIN = "browser-other"
RUN_FILES = ("prepared.json", "camera.y4m", "admin.json", "totp-keys/1")
# The run's own TOTP key (totp_keys.py), as a clone may have none in
# .local/totp-keys: CI runs no `mise run setup`.
TOTP_KEYS = "totp-keys"


def main(argv: list[str]) -> int:
    if argv != ["serve"]:
        print(__doc__, file=sys.stderr)
        return 2
    run, port = Path(os.environ["BROWSER_RUN"]), os.environ["BROWSER_PORT"]
    signal.signal(signal.SIGTERM, stop)
    # A folder that is there already, set beforehand or left by a killed run,
    # is used as it is: only the run's own files go afterwards.
    run.mkdir(exist_ok=True)
    try:
        with new_database() as url:
            prepared = prepare(url, run / "camera.y4m")
            (run / "prepared.json").write_text(json.dumps(prepared))
            (run / TOTP_KEYS).mkdir(exist_ok=True)
            (run / TOTP_KEYS / "1").write_text(totp_keys.new_key())
            os.environ |= {
                "DATABASE_URL": url,
                "APP_ENV": "development",
                "TOTP_KEYS_DIR": str(run / TOTP_KEYS),
                # The build, as production serves it.
                "VITE_DEV_SERVER": "",
                "LOG_LEVEL": "WARNING",
            }
            server.main(["--port", port])
    finally:
        for name in RUN_FILES:
            (run / name).unlink(missing_ok=True)
        for folder in (run / TOTP_KEYS, run):
            with contextlib.suppress(OSError):
                folder.rmdir()
    return 0


def stop(_signal: int, _frame: object) -> None:
    """Playwright stops the server with SIGTERM, to the whole group, which uv
    passes on too: uvicorn shuts down and passes it on to this, which ends
    the process by way of the cleanup in main(), and ignores another."""
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    sys.exit(0)


def prepare(url: str, video: Path) -> dict[str, str]:
    """The database migrated and seeded, a code for the tests' device, and
    the camera's video: what the tests need."""
    password = secrets.token_urlsafe(16)
    with migrated_engine(url) as engine, Session(engine) as session, session.begin():
        if seed.run(session, argparse.Namespace(password=password)) != 0:
            raise RuntimeError("the seed refused the new database")
        label = session.scalars(
            select(Label.code).join(Item).where(Item.name == seed.LABELLED)
        ).one()
        write_camera_video(video, label)
        # A duplicate merged into another, so a Retired list has a hint in
        # it (contrast.spec.ts).
        kept = session.scalars(select(Volunteer).order_by(Volunteer.id)).first()
        assert kept is not None  # The seed adds volunteers.
        duplicate = volunteers.add(session, f"{kept.name} (twice)", None)
        volunteers.merge(session, duplicate, kept.id, datetime.now(UTC))
        # Another administrator, whose sign-in links.spec.ts resets.
        other = Admin(username=OTHER_ADMIN)
        session.add(other)
        session.flush()
        session.add(
            AdminPassword(id=other.id, password_hash=accounts.hash_password(password))
        )
        # The one device the tests share, registered by sign-in.setup.ts.
        _, phrase = devices.create_code(
            session, max_uses=1, lifetime=timedelta(hours=1), label="browser tests"
        )
    return {
        "username": seed.USERNAME,
        "password": password,
        "phrase": phrase,
        "labelled": seed.LABELLED,
        "other": OTHER_ADMIN,
    }


def write_camera_video(path: Path, text: str) -> None:
    """A still QR code holding `text`, as the Y4M video Chromium's fake
    camera plays: a full QR code, as labels are printed (decision 0015),
    in black and white."""
    code = segno.make_qr(text, error="m")
    rows = [
        bytes(16 if dark else 235 for dark in row)
        for row in code.matrix_iter(scale=8, border=4)
    ]
    side = len(rows)
    frame = b"FRAME\n" + b"".join(rows) + bytes([128]) * (side * side // 2)
    with path.open("wb") as video:
        video.write(f"YUV4MPEG2 W{side} H{side} F5:1 Ip A1:1 C420jpeg\n".encode())
        video.write(frame * 5)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
