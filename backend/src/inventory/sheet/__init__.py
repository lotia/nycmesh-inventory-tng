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
rule's own part of the printed breakdown.
"""

# A section's report: a heading, and labelled integers beneath it. Integers
# rather than free text is what lets one place align them and keeps six
# classifiers from each inventing their own layout. Most of what a rule
# reports is a partition, but not all of it -- a distinct count and a
# largest-of are neither -- so the contract is a label and a number and no
# more.
#
# **A label's leading spaces carry its depth.** Two spaces is a child of the
# line above it; three is a child of *that*, which is how a line says it is a
# subset of its sibling rather than a further share of the population. The
# rendering right-aligns the numbers and leaves the indent alone, and the test
# that keeps the brief honest compares it, so an indent typed by accident is a
# failure rather than a shrug.
#
# Named here, upstream of every module that implements it, so that a rule can
# annotate itself against its own contract. The alias for the function that
# produces one lives with the registry, in the management command.
type Report = tuple[str, list[tuple[str, int]]]
