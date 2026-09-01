"""Find prose that lives in two files, and report each pair once.

The rule is DEVELOPERS.md#1-one-topic-one-place; check-docs.sh is the entry
point and says what is read and why. This is the reader.

A file of its own rather than a heredoc inside that script, because this is
the largest body of Python docstrings in the repository and the commit that
taught the checker to read Python docstrings left them unread: `commentary`
takes its branch on the filename, so a program embedded in a `.sh` is the one
file exempt from the rule it enforces. scripts/repo-settings.sh and
scripts/check-batch.sh already call out to a .py beside them.
"""

import ast, os, pathlib, re, sys
from itertools import takewhile

WORDS = int(os.environ["WORDS"])
ALLOW = pathlib.Path(os.environ["ALLOW"])


def docstrings(text: str) -> list[str]:
    """The strings a Python file says, as against the ones it uses.

    A string standing alone as a statement is a docstring -- a module's, a
    class's, a function's. A string handed to something is a value, and a
    value is code however much it reads like prose: the trigger bodies two
    migrations install are near-identical SQL, and reporting them as the same
    explanation is the checker being wrong rather than the migrations.
    """
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return re.findall(r'"""(.*?)"""', text, re.S)
    return [
        node.value.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    ]


def commentary(name: str, text: str) -> str:
    """The prose out of a source file: its docstrings and its comment blocks.

    A source file explains itself in comments, and those comments are where an
    explanation gets copied out of a document -- so they are read the same way
    a page is. Code is not prose and is dropped, as a fenced block is in
    Markdown; so are the directives that only look like comments.
    """
    out: list[str] = []
    if name.endswith(".py"):
        out += docstrings(text)
    # TypeScript, and a Helm template, spell a comment this way. The leading
    # asterisks of a JSDoc block are decoration rather than words.
    out += [b.replace("*", " ") for b in re.findall(r"/\*(.*?)\*/", text, re.S)]
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("//"):
            out.append(stripped.lstrip("/").strip())
            continue
        if not stripped.startswith("#"):
            continue
        if stripped.startswith(("#!", "# shellcheck", "# type:", "# noqa")):
            continue
        out.append(stripped.lstrip("#").strip())
    return " ".join(out)


def prose(text: str) -> str:
    """What is left when everything that is not an explanation is removed."""
    text = re.sub(r"^---\n.*?\n---\n", " ", text, flags=re.S)   # front matter
    text = re.sub(r"```.*?```", " ", text, flags=re.S)          # fenced code
    text = re.sub(r"`[^`]*`", " ", text)                        # inline code
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.S)         # comments
    # The whole link, label included. A label is addressing rather than
    # explanation -- two files citing the same work, or both pointing at
    # "Definition of Done", are doing what the rule asks for, not breaking it.
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", " cite ", text)
    # A bare path, URL or record number is the same addressing without the
    # brackets, which is the form a citation takes in a comment. Two files
    # pointing a reader at docs/decisions/0016-invariants-for-every-writer.md,
    # or both saying "decision 0016", are obeying the rule rather than
    # breaking it -- and a Markdown page saying so in a link already reads as
    # nothing at all, so leaving these in would compare a page against a
    # comment with the citation deleted from only one side.
    #
    # Every form becomes the same single word rather than nothing. Deleting
    # them shortened the surrounding prose, so an inline citation bought a
    # two-word discount on the window and two files could share an
    # explanation of eleven words plus a reference in silence. One word each
    # keeps the four forms symmetrical and keeps a run the length it is.
    # A citation on its own is still far under the window, which is what
    # stripping was for.
    #
    # The anchor goes with the path: without it `#rolling-back allows either`
    # kept the anchor's words as prose, and a line beginning with what was
    # left was then dropped by the heading filter below -- deleting real
    # prose from one side of the comparison.
    text = re.sub(r"https?://\S+", " cite ", text)
    text = re.sub(r"[\w.@-]*/[\w./@-]*\.[a-z]{2,4}(?:#[\w-]+)?", " cite ", text)
    text = re.sub(r"\bdecisions?\s+\d{4}\b", " cite ", text, flags=re.I)
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
    """The files each allowed passage was granted for, and its prose.

    Any number of them rather than two, because the same label on N modules
    cost N(N-1)/2 entries when an allowance covered a pair -- half of one
    batch's growth here was that bookkeeping rather than judgement.

    The paths are the leading lines with no space in them, which is what tells
    them from the passage: a passage is at least a dozen words.

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
        paths = list(takewhile(lambda ln: " " not in ln.strip(), (ln.strip() for ln in lines)))
        passage = " ".join(lines[len(paths) :])
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
for named, passage in granted:
    for run in runs(passage.split()):
        allowed.setdefault(run, []).append(named)

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
    # WHAT WILL NOT DECODE CANNOT HOLD REPEATED PROSE, and that is the question
    # actually being asked -- so it is asked of the bytes rather than of the
    # name. The pattern in check-docs.sh names extensions, and no list of them
    # is ever complete: a stray trace.pb and a directory of parquet files left
    # in the root by a profiling run matched none of it, and every run of the
    # checker then ended in UnicodeDecodeError saying nothing had been checked
    # at all -- which reads as the guard being broken rather than as a file it
    # should have stepped over. inventory-tng-2aor.
    #
    # utf-8 BY NAME rather than the locale's encoding, so the corpus is the
    # same file here and in CI. One that decoded under one locale and not the
    # other would make this answer differently in the two places it runs.
    #
    # AND OSError WITH IT, because "cannot be read" is the same answer arriving
    # by a different route. A file with no read permission raised through the
    # first version of this guard and took the whole run down with "the reader
    # failed, so nothing was checked" -- which is the precise failure the bead
    # above was filed about, left standing by a guard that named only one of
    # its two causes.
    #
    # SAID, NOT SWALLOWED. A skip that prints nothing is this rule quietly
    # ceasing to apply to a file: a page saved as CP-1252 -- one smart quote is
    # enough -- would be dropped and the run would still report that no prose is
    # repeated anywhere. The file three functions up says the same thing about a
    # malformed allowance, for the same reason. A note rather than a failure,
    # because a stray binary is not somebody's mistake to fix.
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError) as exc:
        print(f"note {name} could not be read as utf-8 text, so nothing in it was checked")
        print(f"note   {type(exc).__name__}: a binary file is expected here; a page is not")
        continue
    if name.endswith(".md"):
        words = prose(text).split()
    else:
        # Which comment a language spells with what is commentary()'s to know.
        words = prose(commentary(name, text)).split()
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
            if any({first, name} <= named for named in allowed.get(run, ())):
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
    print("note   the paths, one per line, then the passage they share")

for named, passage in granted:
    absent = [name for name in sorted(named) if name not in where]
    if absent:
        print("note an allowance names " + ", ".join(absent) + ", which is not read here")
        continue
    shared = set(runs(passage.split()))
    if not all(shared & shingles[name] for name in named):
        print("note " + " and ".join(sorted(named)) + " no longer repeat each other:")
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

