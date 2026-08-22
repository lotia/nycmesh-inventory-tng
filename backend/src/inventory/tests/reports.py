"""Reading a printed section back, and the question both report suites ask of one.

`profile_sheet` and `import_sheet` both print sections built to the `Report`
contract in `inventory/sheet/`, and the same two things have to be asked of
either: what a printed block says, and whether the depths in it are ones the
contract allows. Here rather than in one of the two suites, so that the second
one written did not get to answer them differently.

Here rather than in ``sheets.py`` for the reason that file gives about
``helpers.py``: nothing in it builds a sheet, and a report is not a workbook.
"""


def counts_in(lines: list[str]) -> list[tuple[str, int]]:
    """A printed block read back as the labels and numbers that made it.

    The right-aligned number comes off the end and the label keeps its indent,
    because the indent is what says a line is a subset rather than a share --
    a review found one of those wrong too.
    """
    read = []
    for line in lines:
        label, _, count = line.rpartition("  ")
        read.append((label.rstrip()[2:], int(count)))
    return read


def shares_of(counted: list[tuple[str, int]], parent: str) -> list[tuple[str, int]]:
    """The lines the contract reads as shares of `parent`, which sum to it.

    Two deeper, up to the next line at `parent`'s own depth or shallower. A
    line one deeper is a subset of the line above it rather than another slice
    of the same rows, so it is not one of these and neither is anything under
    one.
    """
    labels = [label for label, _ in counted]
    depth = len(parent) - len(parent.lstrip())
    found = []
    for label, count in counted[labels.index(parent) + 1 :]:
        indent = len(label) - len(label.lstrip())
        if indent <= depth:
            break
        if indent == depth + 2:
            found.append((label, count))
    return found


def depths_are_allowed(counted: list[tuple[str, int]]) -> None:
    """Every line of a section sits at a depth the contract admits.

    Three sections got this wrong in review, and no test comparing a printed
    block against the code that printed it can see it, so the rule on `Report`
    is checked here rather than described there: a line sits at 0, or beside
    the line above it, or two past an ancestor as a share of it, or one past
    the line above it as a subset of that line.
    """
    ancestors = [0]
    previous = 0
    for label, _ in counted:
        indent = len(label) - len(label.lstrip())
        allowed = {0, previous, previous + 1} | {depth + 2 for depth in ancestors}
        assert indent in allowed, f"{label!r} is indented {indent}, and {sorted(allowed)} were the options"
        ancestors = [depth for depth in ancestors if depth < indent] + [indent]
        previous = indent
