"""The templates' words, read through Jinja's i18n extension
(web/template_phrases.py, docs/decisions/0022)."""

from inventory_tng.web.pages import templates


def test_a_templates_values_are_escaped_and_its_lines_read_as_one() -> None:
    said = templates.env.from_string(
        "{% trans name=name %}Start\n  counting {{ name }}{% endtrans %}"
    ).render(name="<b>")
    assert said == "Start counting &lt;b&gt;"


def test_a_plurals_values_may_take_any_name() -> None:
    """Even plural and context, which Jinja passes ngettext by position."""
    said = templates.env.from_string(
        "{% trans count=n, plural=p, context=c %}{{ count }} {{ plural }} {{ context }}"
        "{% pluralize %}{{ count }} {{ plural }}s {{ context }}{% endtrans %}"
    ).render(n=1000, p="box", c="here")
    assert said == "1000 boxs here"
