"""Tests read a page through parsed readers, never its markup as text,
unless the exact markup is the point and a comment says why (CONTRIBUTING.md,
Testing): the backend's through tests/helpers/markup.py, the frontend's
through the DOM and locators (inventory-tng-lof9)."""

import ast
import io
import re
import tokenize
from collections.abc import Iterator
from pathlib import Path

import pytest

from inventory_tng.core.local_env import CLONE

TESTS = Path(__file__).parents[1]
# The readers themselves, and their tests, hold markup on purpose.
EXEMPT = {Path("helpers/markup.py"), Path("harness/test_markup.py")}
# The comment that lets a check read markup as text, and its reason.
MARKER = re.compile(r"Exact markup on purpose:\s*\S")
FIX = (
    "read it parsed (CONTRIBUTING.md, Testing), or, if the exact markup is"
    " the point, say why in a comment on or just above it:"
    " Exact markup on purpose: <the reason>"
)

# A tag, such as <td, </td or <!doctype, but not a pattern's (?P<name> or
# (?<!x), or an attribute's text, such as class=" or data-copy='.
MARKUP = re.compile(r"(?<!\(\?P)(?<!\(\?)</?!?[A-Za-z][\w-]*|[A-Za-z][\w:-]*=[\"']")
COMPARISONS = (ast.In, ast.NotIn, ast.Eq, ast.NotEq)
# Methods whose arguments are looked for in the text they are called on.
SEARCHES = {
    "count",
    "endswith",
    "find",
    "index",
    "partition",
    "rpartition",
    "rsplit",
    "split",
    "startswith",
}
# Functions of the re module, whose first argument is a pattern.
PATTERNS = {"compile", "findall", "finditer", "fullmatch", "match", "search", "sub"}

# A read of the page's markup as text in a frontend test: innerHTML or
# outerHTML not being assigned, getHTML(), Playwright's content(), Testing
# Library's prettyDOM(), or a snapshot. Assigning innerHTML is setup, so
# allowed.
READS = re.compile(
    r"\.(?:inner|outer)HTML\b(?!\s*\+?=(?!=))"
    r"|\.get(?:Inner)?HTML\("
    r"|\.content\(\)"
    r"|\bprettyDOM\("
    r"|\.toMatch(?:Inline|File)?Snapshot\("
)
# A line that is only a comment, which may name a read in prose.
COMMENT = re.compile(r"\s*(?://|/\*|\*)")
FRONTEND = CLONE / "frontend"
FRONTEND_TESTS = (
    "src/**/*.test.ts",
    "src/**/*.test.tsx",
    "src/testing/*",
    "browser-tests/**/*.ts",
)


def own_nodes(statement: ast.stmt) -> Iterator[ast.AST]:
    """The nodes of a statement's own expressions, not of the statements in
    its body."""
    stack: list[ast.AST] = [statement]
    while stack:
        node = stack.pop()
        children = [
            child
            for child in ast.iter_child_nodes(node)
            if not isinstance(child, ast.stmt)
        ]
        yield from children
        stack.extend(children)


def searched(node: ast.AST) -> list[ast.expr]:
    """What a node holds against something: a comparison's operands, a
    search's arguments, a pattern."""
    if isinstance(node, ast.Compare):
        if any(isinstance(op, COMPARISONS) for op in node.ops):
            return [node.left, *node.comparators]
    elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        if isinstance(node.func.value, ast.Name) and node.func.value.id == "re":
            return node.args[:1] if node.func.attr in PATTERNS else []
        if node.func.attr in SEARCHES:
            return node.args
    return []


def strings(node: ast.expr) -> Iterator[tuple[int, str]]:
    """The line and text of each string constant an operand is made of:
    itself, the parts of an f-string or a concatenation, and the members of
    a literal collection, but not the arguments of a call, its input."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str | bytes):
        value = node.value
        yield node.lineno, value if isinstance(value, str) else value.decode("latin-1")
    elif isinstance(node, ast.JoinedStr):
        for part in node.values:
            yield from strings(part)
    elif isinstance(node, ast.Tuple | ast.List | ast.Set):
        for part in node.elts:
            yield from strings(part)
    elif isinstance(node, ast.BinOp):
        yield from strings(node.left)
        yield from strings(node.right)


def explained(comments: dict[int, str], alone: set[int], first: int, last: int) -> bool:
    """Whether an exact-markup comment is on a line from first to last, or
    on the lines of comments alone directly above first. `comments` holds
    each line's comment, or the whole line, by line number from 1."""
    while first - 1 in alone:
        first -= 1
    return any(MARKER.search(comments.get(line, "")) for line in range(first, last + 1))


def markup_compared(source: str) -> list[tuple[int, str]]:
    """The line, and the markup, of each tag or attribute a Python test
    compares as text, with no exact-markup comment from its statement's
    first line to the markup's, or directly above the statement."""
    comments = {
        token.start[0]: token.string
        for token in tokenize.generate_tokens(io.StringIO(source).readline)
        if token.type == tokenize.COMMENT
    }
    lines = source.splitlines()
    alone = {
        number for number in comments if lines[number - 1].lstrip().startswith("#")
    }
    statements = (n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.stmt))
    found = {
        (line, match.group())
        for statement in statements
        for node in own_nodes(statement)
        for operand in searched(node)
        for line, text in strings(operand)
        if (match := MARKUP.search(text))
        and not explained(comments, alone, statement.lineno, line)
    }
    return sorted(found)


def markup_read(source: str) -> list[tuple[int, str]]:
    """The line, and the read, of each place a frontend test reads markup as
    text with no exact-markup comment on its line or directly above it. A
    line that is only a comment reads nothing."""
    lines = dict(enumerate(source.splitlines(), start=1))
    alone = {number for number, line in lines.items() if COMMENT.match(line)}
    return [
        (number, match.group())
        for number, line in lines.items()
        if number not in alone
        and (match := READS.search(line))
        and not explained(lines, alone, number, number)
    ]


@pytest.mark.parametrize(
    "source",
    [
        'assert "<td>2</td>" in page',
        "assert f'<a href=\"{address}\">' not in page.text",
        "assert page.count('class=\"alert\"') == 1",
        'assert page.text.startswith("<!doctype")',
        'if "<main" in said:\n    pass',
        'assert x in said.split("<main", 1)[1]',
        'rows = re.findall(r"<tr>(.*?)</tr>", page)',
        'assert said == "Start " + "<b>"',
        '# Exact markup on purpose: x\n\nassert "<b>" not in page',
        'assert "<b>" not in page  # Exact markup on purpose',
        'assert b"<td" in response.content',
        'assert re.search(r"(?P<id>\\d+)<td", page)',
    ],
)
def test_markup_compared_as_text_is_found(source: str) -> None:
    assert markup_compared(source)


@pytest.mark.parametrize(
    "source",
    [
        'html = render("<td>2</td>")\nassert read(html).text("td") == "2"',
        'assert read(\'<a href="/x">X</a>\').links() == [("X", "/x")]',
        'assert said == "Start counting"',
        'assert "said 250 m" in page',
        'assert x == "a < b"',
        "assert read(page).select('a[href=\"/x\"]') == []",
        'assert re.fullmatch(r"(?P<id>\\d+)(?<!0)", said)',
        '# Exact markup on purpose: escaped.\nassert "<b>" not in page',
        '# Exact markup on purpose: escaped,\n# never a tag.\nassert "<b>" not in page',
        'assert "<b>" not in page  # Exact markup on purpose: escaped.',
        'assert (\n    # Exact markup on purpose: escaped.\n    "<b>" not in page\n)',
    ],
)
def test_markup_given_as_input_or_explained_is_not_found(source: str) -> None:
    assert markup_compared(source) == []


def test_no_backend_test_compares_markup_as_text() -> None:
    found = [
        f"{path.relative_to(TESTS)}:{line}: {markup!r}, {FIX}"
        for path in sorted(TESTS.rglob("*.py"))
        if path.relative_to(TESTS) not in EXEMPT
        for line, markup in markup_compared(path.read_text())
    ]
    assert found == []


@pytest.mark.parametrize(
    ("source", "read"),
    [
        ("expect(form.innerHTML).toContain('<b>');", ".innerHTML"),
        ("const html = node.outerHTML;", ".outerHTML"),
        ("expect(node.innerHTML === '').toBe(true);", ".innerHTML"),
        ("const html = document.body.getHTML();", ".getHTML("),
        ("expect(await page.content()).toContain('x');", ".content()"),
        ("expect(container).toMatchSnapshot();", ".toMatchSnapshot("),
        ("expect(x).toMatchInlineSnapshot(`<p/>`);", ".toMatchInlineSnapshot("),
        ("const html = await row.innerHTML();", ".innerHTML"),
        ("await row.evaluate((e) => e.outerHTML);", ".outerHTML"),
        ("expect(prettyDOM(container)).toContain('x');", "prettyDOM("),
        (
            "// Exact markup on purpose: why.\n\nconst html = node.outerHTML;",
            ".outerHTML",
        ),
    ],
)
def test_frontend_markup_reads_are_found(source: str, read: str) -> None:
    """Each read is on the source's last line."""
    assert markup_read(source) == [(len(source.splitlines()), read)]


@pytest.mark.parametrize(
    "source",
    [
        "// Never reads its .innerHTML, only the DOM.",
        "/**\n * Not its .outerHTML.\n */",
        "document.body.innerHTML = `<p>Hint</p>`;",
        "document.body.innerHTML += '<p>Hint</p>';",
        "// Exact markup on purpose: why.\nconst html = node.outerHTML;",
        "const html = node.outerHTML; // Exact markup on purpose: why.",
    ],
)
def test_frontend_setup_prose_or_explained_reads_are_not_found(source: str) -> None:
    assert markup_read(source) == []


@pytest.mark.clone
def test_no_frontend_test_reads_markup_as_text() -> None:
    paths = {path for pattern in FRONTEND_TESTS for path in FRONTEND.glob(pattern)}
    found = [
        f"{path.relative_to(CLONE)}:{line}: {read}, {FIX}"
        for path in sorted(paths)
        for line, read in markup_read(path.read_text())
    ]
    assert found == []
