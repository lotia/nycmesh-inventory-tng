"""Which form a page's values and problems are from is said by its route
(the item page's form_sent_back, a page's sent_back), never guessed by a
template from a key in `values`, as 'version' in values once was: a guess
breaks when another form sends that key too, and marks or fills the
wrong form's boxes (inventory-tng-1rmm)."""

import re

from helpers.source import TEMPLATES

# Exact markup on purpose: the rule is about what templates write. It
# finds a quoted key tested for in `values`, the guess templates made;
# other guesses, such as values.get('version') as a condition, are left
# to review.
GUESS = re.compile(r"""['"][\w-]+['"]\s+(?:not\s+)?in\s+values\b""")


def test_no_template_guesses_the_form_sent_from_its_values() -> None:
    guesses = [
        f"{path.relative_to(TEMPLATES)}:{source.count('\n', 0, found.start()) + 1}"
        for path in sorted(TEMPLATES.rglob("*.html"))
        for source in [path.read_text()]
        for found in GUESS.finditer(source)
    ]
    assert guesses == []
