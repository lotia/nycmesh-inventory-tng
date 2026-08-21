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
"""
