"""A form's own problems carry their kind, so the page that shows them
counts them (web/form_problems.py, Ali, 2026-11-27, inventory-tng-v8sq):
a forms module (forms.py, or one named for its form, *_forms.py) that
puts a phrase of its own into a problems dict bare, rather than as a
Problem, fails here.

Deterministic, read from the source: only `problems[box] = NAME` is read,
NAME a module's phrase (a name or a dotted name in capitals), so a value
read from a check (quantity(), name_problem()) passes, as those return a
Problem already. A phrase that is not a problem of the form's own is
named in NOT_A_PROBLEM, with why, and one no longer found fails too."""

import ast
from pathlib import Path

from helpers.source import SOURCE, modules

NOT_A_PROBLEM = {
    # Points at the line to type something else here on: the count's
    # refusal is listed elsewhere (inventory.counts.refused).
    ("stock/count_forms.py", "TYPE_IT_HERE"),
    # A rule's words, said as the form is read: counted as the form's
    # refusal, closed meanwhile (inventory.counts.refused).
    ("stock/count_forms.py", "RULES"),
}


def _forms_modules() -> list[Path]:
    return [
        path
        for path in modules()
        if path.name == "forms.py" or path.name.endswith("_forms.py")
    ]


def _phrase(node: ast.expr) -> str | None:
    """The phrase a value names, if it is one: a constant in capitals, or
    one of a table of them."""
    match node:
        case ast.Name(id=name) if name.isupper():
            return name
        case ast.Attribute(attr=name) if name.isupper():
            return name
        case ast.Subscript(value=table):
            return _phrase(table)
    return None


def _bare(tree: ast.Module) -> set[str]:
    """The phrases `tree` puts into a problems dict bare."""
    return {
        phrase
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Subscript)
            and isinstance(target.value, ast.Name)
            and target.value.id == "problems"
            for target in node.targets
        )
        and (phrase := _phrase(node.value)) is not None
    }


def _found() -> set[tuple[str, str]]:
    return {
        (str(path.relative_to(SOURCE)), phrase)
        for path in _forms_modules()
        for phrase in _bare(ast.parse(path.read_text(), str(path)))
    }


def test_every_problem_a_form_finds_says_its_kind() -> None:
    assert _found() - NOT_A_PROBLEM == set()


def test_every_phrase_left_bare_is_still_there() -> None:
    assert NOT_A_PROBLEM - _found() == set()


def test_finds_a_phrase_left_bare() -> None:
    source = (
        'problems["a"] = PICK_ONE\n'
        'problems["b"] = Problem(PICK_ONE, "not_chosen")\n'
        'problems["c"] = forms.NO_SUCH_ROW\n'
        'problems["d"] = amount\n'
        "problems[CLOSED] = RULES[CLOSED]\n"
        'others["e"] = PICK_ONE\n'
    )
    assert _bare(ast.parse(source)) == {"PICK_ONE", "NO_SUCH_ROW", "RULES"}
