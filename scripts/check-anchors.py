"""References to a page's headings made outside Markdown, held to the page.

A page moving a section is guarded on one side only. lychee follows every link
a Markdown file makes, fragment included, so a page pointing at a heading that
is gone fails the Documentation job. A docstring, a shell comment, a refusal
message or a workflow comment pointing at the same heading is not a link and
is followed by nothing -- and those are the references a reader meets when
something has just refused them, which is the worst moment for one to be
stale. This reads them.

HOW A HEADING BECOMES AN ANCHOR is GitHub's rule, and it is not this
repository's to vary: lowercase the text, drop every character that is not a
letter, a digit, a space, a hyphen or an underscore, turn spaces into hyphens,
and number a repeat. `## Amendment (2026-08-30) — the requirement` is
`#amendment-2026-08-30--the-requirement`, two hyphens where the dash was
dropped between two spaces. Inline code loses its backticks and keeps its
text; a link in a heading keeps its label. lychee applies the same rule to the
pages, so a reference that passes here would pass there written as a link.

TWO SPELLINGS. `<page>.md#<anchor>` is the one a link would use, and it is
held to the anchor rule above. `<page>.md "Heading"` -- or with single quotes
-- is how a shell comment and a refusal message cite a section, because a
heading is easier to find on a page than an anchor is, and it is held to the
heading's text, backticks aside; the quotes may be backslashed, as they are
inside a double-quoted shell string. A quoted heading may break across two comment
lines, and the comment's own prefix on the second is not part of it.

WHERE THE PAGE IS. A reference that starts with `./` or `../` is relative to
the file that makes it, which is how a docstring five directories down reaches
`../../../../../docs/data-model.md`. Anything else is relative to the
repository root, which is how every reference in `scripts/` and every workflow
is written, and is what a reader with the repository open would type -- and
that includes a page under a dot-directory, `.agents/skills/deploy/SKILL.md`,
whose leading dot is a name and not a step.

WHAT IS NOT READ. A reference with no fragment: a file that has moved is
lychee's and `test_documented_commands.py`'s to notice, and a bare path in a
comment is addressing rather than a promise about a section. A heading inside
a fenced block, an indented block or a `<pre>`, which review_cycle.py's
`hidden` already tells apart from one that is shown. And a reference whose
page does not exist at all is reported as that, once, rather than as a missing
heading.

stdlib only, for the reason check-config.py gives: the jobs that run this
install nothing first.
"""

import difflib
import functools
import pathlib
import re
import sys

from review_cycle import hidden

# A page, then either `#anchor` or a quoted heading.
# The quote may carry a backslash: a refusal message inside a double-quoted
# shell string spells a citation `docs/commits.md \"Checking it\"`, and six
# of those went unread through a move -- inventory-tng-uhge.1.
REFERENCE = re.compile(
    r"(?<![\w/.-])((?:\.{1,2}/)?[\w./-]*\w\.md)"
    r"(?:#([\w-]+)| \\?([\"'])([^\"'\\\n]+(?:\n[^\"'\\\n]+)?)\\?\3)"
)
CONTINUATION = re.compile(r"\n[ \t]*(?:#+|//|\*)?[ \t]*")
HEADING = re.compile(r"^#{1,6}[ \t]+(.*?)[ \t]*#*[ \t]*$")

# The working directory, which check-anchors.sh has already made the
# repository root -- and not this file's own location, so that the suite can
# point it at a repository that is not the one it lives in.
REPO_ROOT = pathlib.Path.cwd()


def slug(text: str) -> str:
    """The anchor GitHub gives a heading whose text is `text`."""
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)   # a link keeps its label
    text = re.sub(r"<[^>]+>", "", text)                     # an HTML tag is not text
    kept = "".join(c for c in text.lower() if c.isalnum() or c in " -_")
    return kept.replace(" ", "-")


@functools.lru_cache(maxsize=None)
def headings_of(page: pathlib.Path) -> tuple[frozenset[str], frozenset[str]] | None:
    """The page's heading texts and the anchors they answer to; None when there is no such page."""
    if not page.is_file():
        return None
    lines = page.read_text(encoding="utf-8").splitlines()
    shown = hidden(lines)
    titles = [m.group(1) for n, line in enumerate(lines) if n not in shown and (m := HEADING.match(line))]
    seen: dict[str, int] = {}
    anchors: set[str] = set()
    for title in titles:
        base = slug(title)
        n = seen.get(base, 0)
        seen[base] = n + 1
        anchors.add(base if n == 0 else f"{base}-{n}")
    return frozenset(t.replace("`", "") for t in titles), frozenset(anchors)


def resolve(source: pathlib.Path, target: str) -> pathlib.Path:
    """The page `target` names, from `source`'s point of view."""
    if target.startswith(("./", "../")):
        return (source.parent / target).resolve()
    return (REPO_ROOT / target).resolve()


for name in sys.argv[1:]:
    path = pathlib.Path(name)
    if not path.is_file() or path.is_symlink():
        continue
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        # check-docs.py says why a file that will not decode is stepped over
        # rather than named: a binary is expected in a corpus this wide.
        continue
    if ".md" not in text:
        continue
    missing: set[str] = set()
    for found in REFERENCE.finditer(text):
        target, anchor, _, quoted = found.groups()
        number = text.count("\n", 0, found.start()) + 1
        page = headings_of(resolve(path, target))
        if page is None:
            if target not in missing:
                missing.add(target)
                print(f"fail {name}:{number}: {target} is not a page in this repository")
            continue
        titles, anchors = page
        if anchor is not None:
            wanted, known, shape = anchor, anchors, f"has the anchor #{anchor}"
        else:
            wanted = CONTINUATION.sub(" ", quoted).strip().replace("`", "")
            known, shape = titles, f'is titled "{wanted}"'
        if wanted in known:
            continue
        print(f"fail {name}:{number}: no heading in {target} {shape}")
        near = difflib.get_close_matches(wanted, sorted(known), n=3, cutoff=0.5)
        if near:
            quote = "#{}" if anchor is not None else '"{}"'
            print("note   nearest: " + ", ".join(quote.format(a) for a in near))
