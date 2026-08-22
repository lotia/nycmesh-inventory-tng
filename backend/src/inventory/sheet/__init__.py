"""Reading the exported Google Sheet, and the rules for what its rows mean.

The workbook this reads is the system inventory-tng replaces. Every rule here
has to be applied to every historical row by the importer anyway, so the rule
and the figure it produces live together with the code rather than in prose
that can drift away from it -- which is what
`docs/briefs/sheet-classifiers.md` is for, and why it cites `profile_sheet`
rather than a hand count.

Nothing in this package touches the database or the models. A rule is a
function over what a row says, so it can be tested without a workbook and
without a scene, and `profile_sheet` and the importer both get the same answer
from it.

Every module here also exposes `section(sheet) -> Report`, which is that
rule's own part of the printed breakdown. **A section prints the figures its
rule is quoted for, including the readings its rule rejects**, because a
number the brief argues from that no code produces is the failure this package
exists to stop -- and it is the failure the brief itself kept committing.
"""

from collections.abc import Iterable, Mapping

# A section's report: a heading, and labelled integers beneath it. Integers
# rather than free text is what lets one place align them and keeps six
# classifiers from each inventing their own layout. Most of what a rule
# reports is a partition, but not all of it -- a distinct count and a
# largest-of are neither -- so the contract is a label and a number and no
# more.
#
# **A label's leading spaces carry its depth, and the step says which kind of
# line it is.** Two more spaces than the line it hangs from makes it a *share*
# of that line, and the shares under one parent sum to it. **One** more space
# makes it a *subset* of the line immediately above -- a fact about those rows
# rather than another slice of them -- which is how `of those, naming more
# than one` says it is counted inside the line above rather than beside it.
#
# So an indent is legal when it is 0, beside the line above it, an ancestor's
# plus two, or the previous line's plus one. `test_brief_figures` checks that of every section,
# because the pasted block in the brief matching the code proves the two agree
# and proves nothing about whether either is right.
#
# **A section's labels do not depend on its data.** Where a line is one of an
# enumerated set -- a spelling, a reading -- the set is written down and every
# member gets a line, including the ones this export happens not to hold. A
# label that appeared only when the data held it would make the brief correct
# for one workbook, and the labels are what the brief is checked against.
#
# Named here, upstream of every module that implements it, so that a rule can
# annotate itself against its own contract. The alias for the function that
# produces one lives with the registry, in the management command.
type Report = tuple[str, list[tuple[str, int]]]


def each(members: Iterable[str], counted: Mapping[str, int], indent: str = "  ") -> list[tuple[str, int]]:
    """A line per member of an enumerated set, in the order it is written.

    The rule above, made executable. A section reaching for `Counter.items()`
    instead is exactly the drift the rule forbids -- it prints what the data
    held rather than what the rule admits -- and a named helper makes that
    visible in review rather than a thing to notice.
    """
    return [(f"{indent}{member}", counted.get(member, 0)) for member in members]
