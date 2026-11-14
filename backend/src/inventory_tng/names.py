"""What makes an item's or a package's name its own: its key, the name as
two names are compared (docs/decisions/0021). Lower case, without spaces
and punctuation, so "ToughCable" and "Tough cable" clash; but a decimal
point between two digits is kept, so "305 m" and "30.5 m" do not; a comma
is always punctuation, so "1,000 ft" and "1000 ft" clash (US number
conventions: 0021, 2026-11-12).

Each such row keeps its key in `name_key`, written by the app as the name is
set (models.KeyedName), and unique in an index of its own. A CHECK
constraint holds it to the name in PostgreSQL (name_key_sql), a declaration
within docs/decisions/0016, so a key written wrong, by SQL or a bug, is
refused. So the rule is said twice, in Python and in SQL, from one pattern,
and a test checks they agree on every character. Changing the rule changes
every key: a migration writes the CHECKs again and refills the keys, naming
by id any rows that would then clash.

Only ASCII's spaces and punctuation are ignored, and only ASCII's letters
lowered, whatever the database's locale: as a C locale has it, which
CloudNativePG makes by default, so letters such as `â` and `Â` are kept
apart, and so are signs beyond ASCII, such as an en dash or a curly quote.
"""

import re
import string

from sqlalchemy import ColumnElement, SQLColumnExpression, collate, func

# What a key leaves out, in a pattern PostgreSQL and Python read alike: a
# space (tab to carriage return, and the space) or a punctuation mark of
# ASCII's (C's isspace and ispunct), but not a point between two digits.
# Ranges of code points, not [[:space:][:punct:]], which Python lacks.
IGNORED = r"(?!(?<=[0-9])[.][0-9])[\t-\r -/:-@[-`{-~]"

_IGNORED = re.compile(IGNORED)
_LOWER = str.maketrans(string.ascii_uppercase, string.ascii_lowercase)


def name_key(name: str) -> str:
    """`name`'s key, as the app writes it."""
    return _IGNORED.sub("", name).translate(_LOWER)


def name_key_sql(name: SQLColumnExpression[str]) -> ColumnElement[str]:
    """`name`'s key in SQL, as the CHECK constraints hold it: collated "C",
    so lower() lowers ASCII's letters only, as name_key does, whatever the
    database's locale."""
    return func.lower(func.regexp_replace(collate(name, "C"), IGNORED, "", "g"))
