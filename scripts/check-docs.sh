#!/usr/bin/env bash
# One topic, one place -- the part of it a machine can see.
#
# The rule is in DEVELOPERS.md "Documentation rules": every topic lives in one
# file and everything else links to it. The link checker already catches a link
# that rots. Nothing catches the failure that matters more, which is an
# explanation quietly pasted into a second file, where it stays right until the
# first one changes and then silently disagrees with it.
#
# So: normalise every Markdown file, cut it into overlapping runs of words, and
# report any run that appears in two files. Prose is what is compared. Code
# fences, link targets, headings and tables are dropped, because a repeated
# command or a repeated column header is not a duplicated explanation and
# flagging it would train everybody to ignore this.
#
# Usage: check-docs.sh [--words N] [<path>...]
#
# An allowlist of runs that are meant to be repeated lives in
# scripts/check-docs.allow, one per line, matched after the same normalising.

set -uo pipefail

REPO_ROOT=$(git rev-parse --show-toplevel) || exit 1
cd "$REPO_ROOT" || exit 1

HERE=$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")
. "$HERE/report.sh"

WORDS=12
paths=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --words)
      WORDS=${2:?--words needs a number}
      shift 2
      ;;
    *)
      paths+=("$1")
      shift
      ;;
  esac
done

if [[ ${#paths[@]} -eq 0 ]]; then
  mapfile -t paths < <(git ls-files '*.md')
fi

ALLOW="$REPO_ROOT/scripts/check-docs.allow"

findings=$(WORDS="$WORDS" ALLOW="$ALLOW" python3 - "${paths[@]}" <<'PY'
import os, pathlib, re, sys

WORDS = int(os.environ["WORDS"])
ALLOW = pathlib.Path(os.environ["ALLOW"])


def prose(text: str) -> str:
    """What is left when everything that is not an explanation is removed."""
    text = re.sub(r"^---\n.*?\n---\n", " ", text, flags=re.S)   # front matter
    text = re.sub(r"```.*?```", " ", text, flags=re.S)          # fenced code
    text = re.sub(r"`[^`]*`", " ", text)                        # inline code
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.S)         # comments
    # The whole link, label included. A label is addressing rather than
    # explanation -- two files citing the same work, or both pointing at
    # "Definition of Done", are doing what the rule asks for, not breaking it.
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", " ", text)
    lines = [
        line for line in text.splitlines()
        if not line.lstrip().startswith("#")                    # headings
        and not line.lstrip().startswith("|")                   # tables
    ]
    words = re.findall(r"[a-z0-9']+", " ".join(lines).lower())
    return " ".join(words)


def runs(words: list[str]):
    for i in range(len(words) - WORDS + 1):
        yield " ".join(words[i:i + WORDS])


# Held as whole passages and matched by containment, not as runs of their own:
# a repeated stretch of text is found through whichever window happens to
# straddle it, and that window can reach a word or two past either end of the
# line written here.
def allowances(text: str) -> tuple[list[tuple[frozenset[str], str]], list[str]]:
    """The pairs of files each allowed passage was granted for, and its prose.

    See scripts/check-docs.allow for how an entry is written.
    """
    body = "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )
    out: list[tuple[frozenset[str], str]] = []
    bad: list[str] = []
    for paragraph in re.split(r"\n\s*\n", body):
        lines = [ln for ln in paragraph.splitlines() if ln.strip()]
        if not lines:
            continue
        paths = [ln.strip() for ln in lines[:2]]
        passage = " ".join(lines[2:])
        # Said rather than skipped: a mistyped entry that does nothing leaves
        # the run failing and still telling you to add one.
        if len(paths) < 2 or not passage.strip():
            bad.append(lines[0])
            continue
        out.append((frozenset(paths), prose(passage)))
    return out, bad


# Shingled with the same window as everything else, so an allowance is a
# dictionary lookup rather than a scan of every passage. It is also the more
# exact test: plain containment would let a window match across a word
# boundary, allowing "man example of" because a passage contains "human
# example of".
granted, malformed = allowances(ALLOW.read_text()) if ALLOW.exists() else ([], [])

allowed: dict[str, list[frozenset[str]]] = {}
for pair, passage in granted:
    for run in runs(passage.split()):
        allowed.setdefault(run, []).append(pair)

seen: dict[str, tuple[str, int]] = {}
hits: dict[tuple[str, str], list[tuple[int, int]]] = {}
where: dict[str, list[str]] = {}
shingles: dict[str, set[str]] = {}

for name in sys.argv[1:]:
    path = pathlib.Path(name)
    if not path.is_file():
        continue
    # CLAUDE.md, CODEX.md and GEMINI.md are symlinks to AGENTS.md. A file is
    # not a second copy of itself.
    if path.is_symlink():
        continue
    words = prose(path.read_text()).split()
    where[name] = words
    # A file only duplicates another file. A phrase repeated inside one
    # document is a writing problem, not a single-source-of-truth problem.
    local: set[str] = set()
    shingles[name] = local
    for i, run in enumerate(runs(words)):
        if run in local:
            continue
        local.add(run)
        if run in seen:
            first = seen[run][0]
            if frozenset((first, name)) in allowed.get(run, ()):
                continue
            hits.setdefault((first, name), []).append((seen[run][1], i))
        else:
            seen[run] = (name, i)


def passages(marks: list[tuple[int, int]]) -> list[list[int]]:
    """Group overlapping windows back into the passages they came from.

    Consecutive windows that each advance by one word in both files describe
    one repeated stretch. Reporting the windows would bury a second, unrelated
    duplication between the same pair of files under the first one's noise.
    """
    out: list[list[int]] = []
    for first, second in sorted(marks):
        if out and first == out[-1][0] + out[-1][2] and second == out[-1][1] + out[-1][2]:
            out[-1][2] += 1
        else:
            out.append([first, second, 1])
    return out

# An allowance whose repetition is gone is a baseline outliving its debt, and
# saying so is what stops it. Asked of the allowance itself rather than of what
# collided: with a third copy in play the two allowed files never collide with
# each other -- both collide with the third -- and reading that as "they no
# longer repeat" is exactly backwards.
for line in malformed:
    print("note an allowance is not in the documented form: " + line[:60])
    print("note   two paths, one per line, then the passage they share")

for pair, passage in granted:
    absent = [name for name in sorted(pair) if name not in where]
    if absent:
        print("note an allowance names " + ", ".join(absent) + ", which is not read here")
        continue
    shared = set(runs(passage.split()))
    if not all(shared & shingles[name] for name in pair):
        print("note " + " and ".join(sorted(pair)) + " no longer repeat each other:")
        print("note   the allowance for them can go, with the issue that named it")

if not hits:
    raise SystemExit(0)

for (first, second), marks in sorted(hits.items()):
    for start, _, length in passages(marks):
        # The whole repeated stretch, not the window that happened to find it.
        text = " ".join(where[first][start:start + length + WORDS - 1])
        if len(text) > 160:
            text = text[:157] + "..."
        print(f"fail {first} and {second} say the same thing:")
        print(f'note     "{text}"')

PY
)

while IFS= read -r line; do
  [[ -z "$line" ]] && continue
  case "$line" in
    fail\ *) fail "${line#fail }" ;;
    note\ *) note "${line#note }" ;;
  esac
done <<<"$findings"

verdict "No prose repeated across files in runs of $WORDS words or more." \
  "one of each pair is a link"
