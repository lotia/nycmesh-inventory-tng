#!/usr/bin/env python3
"""The GitHub issues no bead is linked to yet.

`bd` pulls by explicit reference and never enumerates, so nothing in it
notices an issue somebody else filed. Measured on 2026-08-31: a contributor
opened issue 62, `bd github sync --pull-only` reported nothing, and
`bd github pull 62` created the bead. This is the part that finds the 62.

COMPARED AS WHOLE STRINGS, NOT PARSED. GitHub is asked for each issue's `url`
and `bd` stores that same URL in `external_ref`, byte for byte -- checked, not
assumed. So "has this issue been pulled" is a set membership test on the URL
and nothing else: no pattern to get subtly wrong and no prefix to rebuild. An
earlier version matched a regex and had a real bug in it, because issue numbers
are per-repository and the pattern accepted any repository's issue as though it
were ours.

THE REPOSITORY IS TAKEN ALL THE SAME, and not for the matching: `issue_of`
needs it to read a number out of a ref safely, and `refuse_foreign` uses it to
stop a listing that came from somewhere else. Both say why, below.
inventory-tng-cwpa.12.

READ FROM THE COMMITTED EXPORT, NOT THE DATABASE. The Dolt database is
gitignored; `.beads/issues.jsonl` is not. So this gives the same answer in a
fresh clone and in a CI runner that has never seen a database, which is what
`inventory-tng-cwpa.3` needs it to do.

Usage:
    gh issue list --state all --json number,url --jq '.[] | "\\(.number)\\t\\(.url)"' |
        unsynced.py .beads/issues.jsonl lotia/nycmesh-inventory-tng
"""

import json
import re
import sys
from collections.abc import Iterator
from pathlib import Path

#: What every `external_ref` this project makes begins with. Not used to parse
#: one -- see the header -- but to notice that they have all stopped looking
#: like URLs at all, which is what a change in how `bd` records the link would
#: do. That failure is worth catching because it is silent AND it duplicates:
#: no ref would match any URL, every issue would read as unpulled, and a sync
#: would file a second bead for every one of them.
GITHUB = "https://github.com/"

#: Where an issue's number sits in a ref this project recognises. See
#: `number_of`, which is the only thing that may read one.
ISSUE_NUMBER = re.compile(r"/issues/(\d+)$")


def rows(text: str) -> Iterator[tuple[int, dict]]:
    """Each bead in an export, with the line it came from for a message to cite.

    TAKES THE TEXT, NOT THE PATH, because the third reader of this format has
    `bd export` on stdin rather than a file. Cut the other way round, the shared
    unit was a file opener, and the one caller that could not use it wrote its
    own JSON loop and -- the part that mattered -- its own second opinion about
    what a reference looks like.
    """
    for number, line in enumerate(text.splitlines(), start=1):
        if line.strip():
            yield number, json.loads(line)


def ref_of(row: dict, export: Path, number: int) -> str | None:
    """The GitHub URL this bead points at, or None when it points at nothing.

    REFUSES A REF THAT IS NOT A GITHUB URL rather than passing over it. A ref
    means a bead IS linked, and one this cannot recognise is either corruption
    or `bd` having changed how it records the link. Treating it as absent is
    what duplicates: this reader would pull the issue a second time, and
    unexported.py would file a second issue for the bead. One definition,
    because both directions fail the same way and neither may be the lenient
    one.
    """
    ref = row.get("external_ref")
    if not ref:
        return None
    if not ref.startswith(GITHUB):
        raise SystemExit(
            f"{export}:{number} carries an external_ref this does not recognise: {ref!r}\n"
            f"Everything bd has written begins {GITHUB!r}. If that has changed, this would "
            "read every issue as unpulled and file a second bead for each one, so it stops "
            "instead. Teach it the new shape."
        )
    return ref


def number_of(ref: str) -> str | None:
    r"""The issue number a reference names, or None when it names none.

    BESIDE `ref_of` BECAUSE THEY ARE ONE VOCABULARY. `ref_of` says which refs
    are recognised and stops on the rest; this says how to read a recognised
    one. Kept apart, the two drifted within a single run: one accepted a shape
    the other refused outright, so a suite pinned a branch the pipeline could
    never reach.

    ANCHORED TO `/issues/`, and that is the whole of the care here. A bare
    trailing `(\d+)` takes the last run of digits anywhere in the string, so a
    repository URL ending in a digit, or one carrying an `#issuecomment-123`
    fragment, yields a plausible number -- and the caller closes an unrelated
    issue that happens to wear it. None is the safe answer, and callers report
    it rather than guessing.

    Matching an issue to a bead is NOT what this is for -- `linked` does that,
    on whole URLs, because issue numbers repeat across repositories. This is
    only ever asked of a ref already known to be ours, and `issue_of` below is
    how a caller that cannot say that of every row gets to ask it.
    """
    found = ISSUE_NUMBER.search(ref)
    return found.group(1) if found else None


def issue_of(ref: str, repository: str) -> str | None:
    """The issue number a ref names, when the ref is an issue of `repository`.

    THE PAIRING, because `number_of` on its own is not safe to compare with.
    Issue numbers are unique only within one repository, so a number lifted out
    of a ref means nothing until the ref is known to belong to the repository
    the numbers on the other side came from. `number_of`'s own docstring says it
    is only ever asked of a bead already known to be ours; this is how a caller
    iterating a whole export -- where that is not true of every row -- gets to
    ask it anyway.

    THE SHAPE OF THE BUG IT ANSWERS is written down two paragraphs up, in this
    file's header: a reader that matched numbers accepted another repository's
    issue as though it were ours. `drifted.py` reintroduced it by reading a
    number out of every row of the export and looking it up in a listing fetched
    with `--repo`. Nothing could reach it -- every ref in the tracker names this
    repository -- and "latent" described the earlier version too, right up until
    it did not. inventory-tng-cwpa.12.

    COMPARED WITHOUT CASE, because GitHub's owner and repository names are not
    case-sensitive while the URLs it returns are canonically cased. A repository
    named in another casing is the same repository, and reading its own refs as
    somebody else's would be this rule firing on what it exists to permit.

    None when the ref belongs to another repository, and None when it names no
    issue at all: to every caller here those are the same answer -- no number of
    ours to compare -- and neither is an error.
    """
    prefix = f"{GITHUB}{repository}/issues/"
    if not ref.lower().startswith(prefix.lower()):
        return None
    return number_of(ref)


def refuse_foreign(records: list[tuple[int, str]], repository: str) -> None:
    """Stop unless every record offered is an issue of `repository`.

    A LISTING HANDED IN IS A NEW WAY TO BE WRONG. Fetched with `--repo`, the
    records are this repository's by construction; taken from a caller --
    `pull-new-issues.sh --listing` -- nothing says so, and another repository's
    issues would be offered as unpulled and become beads pointing at somebody
    else's work.

    REFUSED, NOT FILTERED. Dropping the foreign rows would answer "everything is
    already linked" about a listing that was never this repository's, which is
    the silent green every guard in this family exists to stop. A caller that
    hands over the wrong file wants to hear about it.
    """
    for _, url in records:
        if issue_of(url, repository) is None:
            raise SystemExit(f"the listing holds {url}, which is not an issue of {repository}.")


def linked(export: Path) -> set[str]:
    """Every GitHub URL some bead already points at."""
    text = export.read_text()
    return {
        ref
        for number, row in rows(text)
        if (ref := ref_of(row, export, number)) is not None
    }


def unseen(offered: list[tuple[int, str]], already: set[str]) -> list[int]:
    """The issue numbers to pull, in the order GitHub gave them, without repeats."""
    out, seen = [], set()
    for number, url in offered:
        if url not in already and url not in seen:
            seen.add(url)
            out.append(number)
    return out


def offered(text: str) -> list[tuple[int, str]]:
    """`number<TAB>value` per line, as `gh issue list` is asked to print it.

    The value is a URL to this reader and a state to `drifted.py`; what both
    need is the same refusal on a line that is not a number and a value, which
    is why the split lives here rather than in each of them.

    The number is carried alongside the URL because it is what
    `bd github pull` takes: `#62` and the full URL are both rejected by its
    `strconv.Atoi`. So this matches on the URL and hands back the number.
    """
    out = []
    for line in text.splitlines():
        if not line.strip():
            continue
        number, tab, url = line.partition("\t")
        if not tab or not number.strip().isdigit() or not url.strip():
            raise SystemExit(f"expected 'number<TAB>url' per line, and got {line!r}")
        out.append((int(number.strip()), url.strip()))
    return out


def main() -> int:
    if len(sys.argv) != 3:
        raise SystemExit(
            f"usage: {Path(sys.argv[0]).name} <path to issues.jsonl> <owner/repository>"
        )
    export, repository = Path(sys.argv[1]), sys.argv[2]
    if not export.exists():
        raise SystemExit(f"{export} does not exist, so nothing here knows what is already linked")

    records = offered(sys.stdin.read())
    refuse_foreign(records, repository)
    for number in unseen(records, linked(export)):
        print(number)
    return 0


if __name__ == "__main__":
    sys.exit(main())
