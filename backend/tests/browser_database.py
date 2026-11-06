"""What the browser tests (frontend/browser/) run against. Playwright's
webServer (playwright.config.ts) starts

    uv run python tests/browser_database.py serve

with BROWSER_RUN, a folder, and BROWSER_PORT set. It makes a database of
its own (databases.py), migrated and seeded, with a registration code for
the tests' device, and in BROWSER_RUN: prepared.json, what the tests need,
and camera.y4m, the QR code of the seed's labelled item for Chromium to
show as its camera. It then serves the built frontend from that database
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
from datetime import timedelta
from pathlib import Path

import segno
from sqlalchemy import select
from sqlalchemy.orm import Session

from inventory_tng import __main__ as server
from inventory_tng import devices, seed
from inventory_tng.db import make_engine
from inventory_tng.models import Item, Label

from databases import migrate, new_database

# What a run writes in its folder: these, and the signed-in session
# sign-in.setup.ts keeps (frontend/browser/prepared.ts, adminState).
RUN_FILES = ("prepared.json", "camera.y4m", "admin.json")


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
            os.environ |= {
                "DATABASE_URL": url,
                "APP_ENV": "development",
                # The build, as production serves it.
                "VITE_DEV_SERVER": "",
                "LOG_LEVEL": "WARNING",
            }
            server.main(["--port", port])
    finally:
        for name in RUN_FILES:
            (run / name).unlink(missing_ok=True)
        with contextlib.suppress(OSError):
            run.rmdir()
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
    engine = make_engine(url)
    try:
        migrate(engine)
        password = secrets.token_urlsafe(16)
        with Session(engine) as session, session.begin():
            if seed.run(session, argparse.Namespace(password=password)) != 0:
                raise RuntimeError("the seed refused the new database")
            label = session.scalars(
                select(Label.code).join(Item).where(Item.name == seed.LABELLED)
            ).one()
            write_camera_video(video, label)
            # The one device the tests share, registered by sign-in.setup.ts.
            _, phrase = devices.create_code(
                session, max_uses=1, lifetime=timedelta(hours=1), label="browser tests"
            )
    finally:
        engine.dispose()
    return {
        "username": seed.USERNAME,
        "password": password,
        "phrase": phrase,
        "labelled": seed.LABELLED,
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
