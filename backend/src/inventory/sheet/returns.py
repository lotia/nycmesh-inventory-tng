"""How often a note says something came back. Not how often it did.

This is the one figure in
[the brief](../../../../docs/briefs/sheet-classifiers.md#what-no-rule-can-recover)
that is deliberately **not** a classifier, and it is here anyway because the
alternative was leaving it a hand count -- which is how it came to be wrong.
It says 68; the reading below gives 52.

**Read
[what no rule can recover](../../../../docs/briefs/sheet-classifiers.md#what-no-rule-can-recover)
before quoting this.** It counts submissions whose note contains the word, and
that is all it counts; the brief says what such a number is and is not, and why
the question behind it belongs to the stakeholder meeting rather than to this
data.

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
