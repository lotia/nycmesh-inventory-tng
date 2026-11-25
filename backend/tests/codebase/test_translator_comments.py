"""A template line holds one translators' comment at most: the extractor
gives each message on a line that line's last comment, so a second one
there puts the first message's comment on the second's, and leaves the
first without one."""

from helpers.source import TEMPLATES


def test_a_template_line_holds_one_translators_comment_at_most() -> None:
    crowded = [
        f"{path.relative_to(TEMPLATES)}:{number}"
        for path in sorted(TEMPLATES.rglob("*.html"))
        for number, line in enumerate(path.read_text().splitlines(), 1)
        if line.count("Translators:") > 1
    ]
    assert crowded == []
