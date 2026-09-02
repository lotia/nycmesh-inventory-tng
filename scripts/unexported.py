#!/usr/bin/env python3
"""The beads that have no GitHub issue yet.

`unsynced.py` answers the other direction -- which issues no bead points at --
and the two together are the whole comparison. This one is what
`export-issues.sh` names to `bd`, and naming it correctly is the entire
non-clobber guarantee rather than a step towards it.

WHY A BEAD WITH NO `external_ref` IS THE SAFE SET, which is
docs/decisions/0031-the-issue-list-is-a-window-on-the-tracker.md point 4 and is
restated here only as far as this file has to enforce it: `bd` decides between
creating an issue and PATCHing one entirely on whether the bead carries a ref.
A PATCH sends the bead's description as the issue's body, replacing whatever
was there. So a list built from beads with no ref cannot reach that path at
all, and the property is visible in the list rather than argued from the code.

The guard on a ref it cannot recognise is `unsynced.py`'s, imported rather than
repeated, because being lenient here files a second issue for a bead that
already has one -- the mirror image of the duplicate that guard was written to
prevent.

READ FROM THE COMMITTED EXPORT, so a fresh clone gives the same answer as the
machine that last synced. That is the property the whole arrangement rests on.

Usage:
    unexported.py .beads/issues.jsonl        # id<TAB>status, one per bead
"""

import sys
from pathlib import Path

from unsynced import ref_of, rows


def unexported(export: Path) -> list[tuple[str, str]]:
    """`(id, status)` for every bead no issue has been filed for, in file order.

    The status travels with the id because the caller needs both and asking
    twice would mean reading the file twice: an export creates every issue
    open -- `bd`'s create call sends no state at all -- so a bead that is
    closed here has to be closed again on GitHub afterwards.

    A row with no id is refused rather than skipped. `bd github sync --issues`
    takes ids, so a bead this could not name is one the export would silently
    leave behind, and "silently left behind" is indistinguishable from "already
    had an issue" in everything downstream.
    """
    out = []
    for number, row in rows(export.read_text()):
        if ref_of(row, export, number) is not None:
            continue
        identifier = row.get("id")
        if not identifier:
            raise SystemExit(f"{export}:{number} is a bead with no id, so nothing could name it")
        out.append((identifier, row.get("status") or "open"))
    return out


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {Path(sys.argv[0]).name} <path to issues.jsonl>")
    export = Path(sys.argv[1])
    if not export.exists():
        raise SystemExit(f"{export} does not exist, so nothing here knows what has been filed")

    for identifier, status in unexported(export):
        print(f"{identifier}\t{status}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
