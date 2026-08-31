#!/usr/bin/env python3
"""Beads that arrived from GitHub and have not been looked at yet.

What such a bead looks like when it lands, and why that is not where it should
stay, is DEVELOPERS.md under "Issue tracking". This is the part that finds them.

WHAT IT KEYS ON, because that is this file's own business: the id `bd` gives a
pulled bead. Triage ends in a rename, so an id still of that shape belongs to
a bead nobody has been through -- which means this needs no state of its own,
and there is no flag to set, clear, or forget.

READ FROM THE COMMITTED EXPORT, for the reason `unsynced.py` gives about its
own: the Dolt database is gitignored and `.beads/issues.jsonl` is not, so this
answers the same in a fresh clone and in CI.

Usage:
    untriaged.py .beads/issues.jsonl
"""

import json
import re
import sys
from pathlib import Path

#: What `bd` names a pulled bead: the workspace prefix, the arrival time in
#: milliseconds, a counter, and a short hex tail. Matched on the SHAPE rather
#: than on the prefix, so a repository that renames its prefix does not
#: silently stop finding arrivals.
ARRIVED = re.compile(r"^.+-\d{13}-\d+-[0-9a-f]{6,}$")

#: Words worth searching the tracker for, to see whether an arrival duplicates
#: something already tracked. Short and common words find everything, which is
#: the same as finding nothing.
NOISE = {
    "the", "and", "for", "with", "that", "this", "from", "into", "when", "what",
    "does", "not", "but", "are", "was", "its", "it's", "has", "have", "can",
    "will", "would", "should", "there", "their", "them", "then", "than",
}


def arrivals(export: Path) -> list[dict]:
    """Every bead still carrying the name it arrived with."""
    found = []
    for line in export.read_text().splitlines():
        if not line.strip():
            continue
        bead = json.loads(line)
        if bead.get("status") == "closed":
            continue
        if bead.get("external_ref") and ARRIVED.match(bead["id"]):
            found.append(bead)
    return found


def terms(title: str, limit: int = 3) -> list[str]:
    """The words in a title most worth searching for.

    Longest first, because a long word is a specific one. `bd search` takes a
    single term and matches it as a substring, so this offers a few rather than
    trying to build one clever query -- a person reading three short lists
    decides faster than they would read one long one.
    """
    words = {w.strip(".,:;!?()[]\"'").lower() for w in title.split()}
    worth = [w for w in words if len(w) > 4 and w not in NOISE]
    return sorted(worth, key=len, reverse=True)[:limit]


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {Path(sys.argv[0]).name} <path to issues.jsonl>")
    export = Path(sys.argv[1])
    if not export.exists():
        raise SystemExit(f"{export} does not exist, so nothing here knows what has arrived")

    waiting = arrivals(export)
    if not waiting:
        print("Nothing is waiting: every bead from GitHub has been given a name.")
        return 0

    print(f"{len(waiting)} bead(s) arrived from GitHub and still carry the name they arrived with.\n")
    for bead in waiting:
        print(f"  {bead['id']}")
        print(f"    {bead.get('title', '')}")
        print(f"    {bead.get('external_ref', '')}")
        for term in terms(bead.get("title", "")):
            print(f"    already tracked?  bd search {term}")
        print(f"    then              bd rename {bead['id']} <prefix>-<name>")
        print("                      bd update <new id> --type=<kind> --priority=<0-4> --parent=<epic>")
        print()

    print("How to decide any of it is docs/triage.md. The commands are the easy half.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
