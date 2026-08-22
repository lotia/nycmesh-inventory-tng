"""Rule 4: the job reference a note names.

The simplest of the six, and the only one whose destination already exists:
`StockTransaction.job_reference` is a field, so once the import has run nothing
has to read prose again to answer "what went into NN217".

Two things about the pattern are latitude rather than accident, and both are
in the ledger rather than imagined:

- **Case is folded**, and it is title case rather than lower case that makes
  it matter.
- **A space is allowed between the letters and the digits.**

No submission writes `NN#217`, so the pattern does not admit a `#`, and the
brief's §4 says why being in the ledger is the test. The digits are unbounded
for the opposite reason -- a limit would be a rule about data already read
rather than about what a job reference is, and it changes no count.

The census of spellings behind all of that, and what each reading costs, is
[§4 of the brief](../../../../docs/briefs/sheet-classifiers.md#4-note-to-job-reference).
It is there and not here because a figure has one home, and `profile_sheet`
is what produces it.
"""

import re
from collections import Counter

from inventory.sheet import Report, each
from inventory.sheet.workbook import Sheet

# NN, an optional space, then the digits. Case-insensitive, per the census
# above.
REFERENCE = re.compile(r"\bNN\s*(\d+)\b", re.IGNORECASE)

# The two narrower readings, kept so the report can price the latitude the
# rule takes rather than leave the census as prose nothing produces.
CASED = re.compile(r"\bNN\s*\d+\b")
TIGHT = re.compile(r"\bNN\d+\b", re.IGNORECASE)

# Every way the letters can be written under this rule. Enumerated for the
# reason `Report` gives.
# The notation rather than any job: a number here would read as a claim about
# that one job. The open box is the space the pattern allows.
SPELLINGS = tuple(f"{letters}{gap}" for letters in ("NN", "Nn", "nN", "nn") for gap in ("", "\u2423"))


def job_reference(note: str) -> str | None:
    """The job this note names, spelled the way the field stores it.

    The first, where a note names two. One does -- `nn498-nn6622`, which is a
    link between two nodes rather than two jobs -- and a field holding one
    string has to choose. The report below counts them so that the choice
    stays visible rather than becoming a silent truncation.
    """
    found = REFERENCE.search(note)
    return f"NN{found.group(1)}" if found else None


def section(sheet: Sheet) -> Report:
    """What the rule finds, and what taking the first reference costs.

    The last two lines are the same submission seen from both ends: a note
    citing two jobs, and a job nothing will carry because of it. Reported
    rather than left implicit, because a partition that quietly loses a row is
    how the figures in the brief stopped agreeing with each other before.
    """
    # How the letters were written, with the space shown as an open box so a
    # reader can see it. Counted over every reference rather than every note,
    # because a note citing two spells each of them.
    written = Counter(
        found.group(0)[: -len(found.group(1))].replace(" ", "\u2423")
        for s in sheet.submissions
        for found in REFERENCE.finditer(s.note)
    )
    # One findall per note, and the first of it is what job_reference returns.
    # Derived rather than called again, so that "the field takes the first" is
    # stated in one place -- job_reference -- rather than encoded twice.
    cited_by = [(s, [f"NN{number}" for number in REFERENCE.findall(s.note)]) for s in sheet.submissions]
    citing = [(s, found) for s, found in cited_by if found]
    cited = {reference for _, found in citing for reference in found}
    imported = {found[0] for _, found in citing}
    several = sum(1 for _, found in citing if len(set(found)) > 1)
    return "Job references", [
        ("submissions citing a job", len(citing)),
        ("distinct jobs cited", len(cited)),
        ("submissions citing more than one", several),
        ("cited jobs the imported field will not carry", len(cited - imported)),
        ("references written", sum(written.values())),
        *each(SPELLINGS, written),
        ("submissions a case-sensitive read would find", sum(1 for s in sheet.submissions if CASED.search(s.note))),
        ("submissions a read allowing no space would find", sum(1 for s in sheet.submissions if TIGHT.search(s.note))),
    ]
