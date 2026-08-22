"""One section of a printed report, as the lines it comes out as.

Shared by the three commands that print sections -- `profile_sheet`, whose
sections are the rules, `import_sheet`, whose sections are the steps, and
`seed_demo_data`, whose one section is what the run added -- for
the reason `_workbook.py` gives about the pair that read a path, and
underscored for the reason it gives about its own name. What a section is, and
what the indent on a label means, is the `Report` contract in
`inventory/sheet/`.
"""


def render(heading: str, counted: list[tuple[str, int]]) -> list[str]:
    """One section as the lines a reader reads it off.

    A function rather than four lines inside a `handle`, because the test that
    keeps the brief's blocks honest has to produce exactly this layout. Built
    into the test instead, it would agree with itself while the command
    changed underneath both -- which is the drift it exists to catch.

    Widths come from the section rather than from a constant, so a rule whose
    labels are longer than today's still lines up and nobody has to come back
    and widen a number here.
    """
    width = max(len(label) for label, _ in counted)
    return [heading, *(f"  {label:<{width}}  {count:>6}" for label, count in counted)]
