"""How often a note says something came back. Not how often it did.

This is the one figure in
[the brief](../../../../docs/briefs/sheet-classifiers.md#what-no-rule-can-recover)
that is deliberately **not** a classifier, and it is here anyway because the
alternative was leaving it a hand count -- which is how it came to be wrong.
It says 68; the reading below gives 52.

Read this before quoting it. Every printed QR opens a form preset to
`Checking Out`, so the ledger's return rate is a property of the instrument
rather than of anybody's behaviour, and a check-in is also how a correction
and a delivery get recorded. So this counts **submissions whose note contains
the word**, which is a floor under "how often did somebody write it down", and
is not a rate of anything. An earlier version of the brief turned this
question into a four-way percentage split; those shares were withdrawn, and
the question they were trying to answer belongs to the stakeholder meeting
(`inventory-tng-8sq`) rather than to this data.

The predicate is the narrowest one there is on purpose: the brief's claim is
that these notes say it *in so many words*, so the rule is the word and
nothing inferred from context. Widening it to `came back` and `brought back`
finds four more and starts guessing.
"""

import re

from inventory.sheet import Report
from inventory.sheet.workbook import Sheet

# The four forms of the word, not `return\w*`: that also takes `returnable`,
# which describes packaging rather than saying anything came back, and the
# whole of this predicate is that the note says it in so many words.
RETURNED = re.compile(r"\breturn(?:s|ed|ing)?\b", re.IGNORECASE)


def says_returned(note: str) -> bool:
    """Whether this note uses the word, in any of its forms."""
    return bool(RETURNED.search(note))


def section(sheet: Sheet) -> Report:
    """The floor, and the check-outs that say it too.

    The check-out line is here because it is the reason the check-in line is a
    floor rather than a rate: six submissions say `return` while recording
    stock going *out*, so the word does not even mean the direction reliably.
    """
    saying = [s for s in sheet.submissions if says_returned(s.note)]
    return "Return language", [
        ("submissions whose note says return", len(saying)),
        ("  recorded as a check-in", sum(1 for s in saying if not s.is_check_out)),
        ("  recorded as a check-out", sum(1 for s in saying if s.is_check_out)),
        ("check-ins", sheet.check_ins),
    ]
