"""The database the browser tests (frontend/browser/) run against, from their
global setup:

    uv run python tests/browser_database.py create VIDEO    # prints JSON
    uv run python tests/browser_database.py drop URL

`create` makes one of its own (databases.py), migrated and seeded, with a
registration code for the tests' devices, and prints what they need. It
also writes VIDEO, for Chromium to show as its camera: the QR code of the
seeded radio's label. One a run leaves behind is dropped by a later run, as
test databases are.
"""

import argparse
import contextlib
import json
import secrets
import sys
from datetime import timedelta
from pathlib import Path

import segno
import structlog
from databases import create_database, drop_database, drop_left_behind, migrate
from sqlalchemy import select
from sqlalchemy.orm import Session

from inventory_tng import devices, seed
from inventory_tng.db import make_engine
from inventory_tng.models import Item, Label


def main(argv: list[str]) -> int:
    # Logs are for people: stdout is for the tests.
    structlog.configure(logger_factory=structlog.PrintLoggerFactory(sys.stderr))
    if argv[:1] == ["drop"] and len(argv) == 2:
        drop_database(argv[1])
        return 0
    if argv[:1] != ["create"] or len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    drop_left_behind()
    url = create_database()
    engine = make_engine(url)
    migrate(engine)
    password = secrets.token_urlsafe(16)
    with Session(engine) as session, session.begin():
        # Its words are for people: stdout is the JSON alone.
        with contextlib.redirect_stdout(sys.stderr):
            seeded = seed.run(session, argparse.Namespace(password=password))
        if seeded != 0:
            return 1
        radio = session.scalars(
            select(Label.code).join(Item).where(Item.name == "LiteBeam 5AC Gen2")
        ).one()
        write_camera_video(Path(argv[1]), radio)
        # One device for each browser the tests run in.
        _, phrase = devices.create_code(
            session, max_uses=10, lifetime=timedelta(hours=1), label="browser tests"
        )
    engine.dispose()
    print(
        json.dumps(
            {
                "database_url": url,
                "username": seed.USERNAME,
                "password": password,
                "phrase": phrase,
            }
        )
    )
    return 0


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
