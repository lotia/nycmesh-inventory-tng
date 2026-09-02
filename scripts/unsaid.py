#!/usr/bin/env python3
"""What a bead holds that its GitHub issue body cannot.

`bd github push` sends a title, a description and labels for type and priority.
It sends nothing else, so `design`, `acceptance_criteria`, `notes` and every
dependency stay in `.beads/issues.jsonl` -- and a contributor who has never run
`bd`, which is everybody reading on GitHub, cannot see what a piece of work
depends on or what would make it done. `inventory-tng-cwpa.15`, and the
amendment to
docs/decisions/0031-the-issue-list-is-a-window-on-the-tracker.md point 2 is
where the decision to widen the window is argued.

THE BODY IS NOT AVAILABLE TO WRITE INTO, and that is the whole reason this
composes a COMMENT. Reconciliation runs `--prefer-newer`, so a GitHub body that
is newer than its bead replaces that bead's description -- 0031 point 6, and
measured. Anything this wrote into a body would come back on the next pull and
BECOME the description, which turns a window into a second copy that overwrites
the original. Nothing `bd` does reads or writes issue comments in either
direction, so a comment is the one place text can sit beside the body without
travelling back.

GENERATED, NEVER READ BACK. This is a projection of the export and nothing
here, or anywhere else, ever parses it again -- which is what keeps 0031 point
2's argument intact: the asymmetry it accepted was against inventing "a second
half-tracker to keep in step", and a rendering that is overwritten wholesale
from the export each time is not one. The tracker is still the record.

EVERY BEAD WITH AN ISSUE IS LISTED, INCLUDING THE SILENT ONES, and that is not
padding. A bead whose last dependency or acceptance criterion is removed stops
having anything to say -- and the comment already sitting on its issue then
says something the tracker no longer does. Listing only the talkative ones left
the caller no way to learn that such an issue exists, so the stale comment would
stay for ever. The caller writes for `say` and removes for `silent`.

Usage:
    unsaid.py .beads/issues.jsonl lotia/nycmesh-inventory-tng
        id<TAB>issue number<TAB>say|silent, for every bead with an issue of ours

    unsaid.py .beads/issues.jsonl lotia/nycmesh-inventory-tng inventory-tng-abc
        the comment body for that one bead, or nothing at all
"""

import sys
from pathlib import Path

from unsynced import issue_of, ref_of, rows

#: How the comment is found again, so a second run rewrites the one it left
#: rather than adding another. `scripts/say-batch.sh` carries the same
#: arrangement for the same reason; this is a different marker because the two
#: live on different things and a script must never edit the other's comment.
#: Invisible in the rendered page, so it costs a reader nothing.
MARKER = "<!-- bead-fields -->"

#: The prose a bead carries and an issue body does not, and what to call each on
#: GitHub. `acceptance_criteria` is titled for the question it answers rather
#: than for the field it comes from: a reader there has no bead vocabulary, and
#: "Done when" is the whole of what that field is for.
PROSE = (
    ("design", "Design"),
    ("acceptance_criteria", "Done when"),
    ("notes", "Notes"),
)

#: Dependency types this renders, and the sentence each becomes. `parent-child`
#: is handled separately because it is the only one that reads differently from
#: each end -- the child says what it is part of, the parent lists what it
#: holds.
RELATIONS = {
    "blocks": "Blocks",
    "related": "Related to",
    "discovered-from": "Discovered from",
}


def indexed(text: str, export: Path) -> tuple[dict[str, dict], dict[str, list[str]]]:
    """`({id: row}, {parent id: [child id]})`, in one pass over the export.

    THE CHILDREN CANNOT BE READ OFF ONE ROW, which is why this exists at all: a
    `parent-child` dependency is recorded on the CHILD, naming the parent it
    depends on. So an epic's row says nothing about what it holds, and the only
    way to render "what this is made of" is to have seen every row first.

    A row with no id is refused rather than skipped, which is the same policy
    `unexported.py` and `drifted.py` apply to the same row: a bead nothing can
    name is one nothing downstream can act on, and leaving it out silently is
    indistinguishable from it having nothing to say.
    """
    by_id: dict[str, dict] = {}
    children: dict[str, list[str]] = {}
    for number, row in rows(text):
        identifier = row.get("id")
        if not identifier:
            raise SystemExit(f"{export}:{number} is a bead with no id, so nothing could name it")
        by_id[identifier] = row
        # `ref_of` is asked of every row, and it is asked for its REFUSAL: it
        # stops on an `external_ref` shaped like nothing bd writes, which is the
        # failure that would otherwise make this quietly render fewer links.
        ref_of(row, export, number)
        for dep in row.get("dependencies") or []:
            if dep.get("type") != "parent-child":
                continue
            child, parent = dep.get("issue_id"), dep.get("depends_on_id")
            if child and parent:
                children.setdefault(parent, []).append(child)
    return by_id, children


def names(identifier: str, by_id: dict[str, dict], repository: str) -> str:
    """How to write another bead down: `#41` when GitHub has it, else its id.

    `#41` RATHER THAN A URL, because GitHub renders it as a link AND records a
    cross-reference on the issue named -- which is the point rather than a side
    effect. An epic reading as a description with no children is what
    `inventory-tng-cwpa.15` is about, and the timeline entry on each child is
    half of the answer.

    THE NUMBER IS ONLY EVER OURS. `unsynced.issue_of` pairs the number with the
    repository that issued it, so a bead pointing at somebody else's issue falls
    back to its id rather than naming an unrelated issue of ours that happens to
    wear that number -- `inventory-tng-cwpa.12` is the whole argument, and this
    is a caller that would have made the same mistake.
    """
    row = by_id.get(identifier)
    ref = row.get("external_ref") if row else None
    number = issue_of(ref, repository) if ref else None
    return f"#{number} (`{identifier}`)" if number else f"`{identifier}`"


def comment(identifier: str, by_id: dict[str, dict], children: dict[str, list[str]],
            repository: str) -> str:
    """The whole comment for one bead, or `""` when it has nothing to add.

    EMPTY IS A REAL ANSWER and callers act on it: a bead whose issue body says
    everything the bead does needs no comment, and roughly a third of the
    tracker is in that position. Returning a heading with nothing under it would
    put a permanent empty section on those issues.
    """
    row = by_id.get(identifier)
    if row is None:
        raise SystemExit(f"no bead in the export is called {identifier}")

    parts: list[str] = []
    for field, title in PROSE:
        if row.get(field):
            parts += [f"**{title}**", "", str(row[field]).strip(), ""]

    links: list[str] = []
    for dep in row.get("dependencies") or []:
        kind, on = dep.get("type"), dep.get("depends_on_id")
        if not on or dep.get("issue_id") != identifier:
            continue
        if kind == "parent-child":
            links.append(f"**Part of** {names(on, by_id, repository)}")
        elif kind in RELATIONS:
            links.append(f"**{RELATIONS[kind]}** {names(on, by_id, repository)}")

    held = children.get(identifier) or []
    if held:
        links.append("**Holds** " + ", ".join(names(c, by_id, repository) for c in held))

    if not parts and not links:
        return ""

    out = [MARKER, "### What the tracker holds that this issue does not", ""]
    out += parts
    if links:
        out += [*links, ""]
    # AN ABSOLUTE URL, because a relative one is resolved against the page a
    # comment is rendered on rather than against the repository, and the
    # repository is a thing this already knows.
    decision = (
        f"https://github.com/{repository}/blob/main/docs/decisions/"
        "0031-the-issue-list-is-a-window-on-the-tracker.md"
    )
    out += [
        f"Written from `.beads/issues.jsonl` for `{identifier}`, which is the record; this is a "
        f"copy of part of it. [Why it is a comment and not the description]({decision})."
    ]
    return "\n".join(out)


def main() -> int:
    if len(sys.argv) not in (3, 4):
        raise SystemExit(
            f"usage: {Path(sys.argv[0]).name} <issues.jsonl> <owner/repository> [<bead>]"
        )
    export, repository = Path(sys.argv[1]), sys.argv[2]
    if not export.exists():
        raise SystemExit(f"{export} does not exist, so nothing here knows what the tracker says")

    by_id, children = indexed(export.read_text(), export)

    if len(sys.argv) == 4:
        print(comment(sys.argv[3], by_id, children, repository), end="")
        return 0

    for identifier, row in by_id.items():
        ref = row.get("external_ref")
        number = issue_of(ref, repository) if ref else None
        if not number:
            continue
        said = "say" if comment(identifier, by_id, children, repository) else "silent"
        print(f"{identifier}\t{number}\t{said}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
