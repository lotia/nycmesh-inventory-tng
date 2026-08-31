"""A field the API may omit is one the client's types declare as optional.

Two serializers withhold from a caller with no account, and each names what it
withholds in a `PERSONAL` tuple. `frontend/src/api/types.ts` is hand-written
and its own header says `backend/openapi.yaml` is the contract it must agree
with -- and it disagreed with it, for three fields, from
`inventory-tng-81f7.5` until `inventory-tng-aoji.2`.

WHY A `?` AND NOT A `| null`. The backend pops the key, so a withheld address
arrives as `undefined` rather than `null`. A component written against
`email: string | null` will check `=== null`, that check passes straight
through `undefined`, and the branch meant to handle "we were not told" runs the
"we were told nothing is there" path instead. Both are absences and they are
not the same absence.

Nothing broke while it was wrong, because the only readers are the
administrator's screens and those are signed in. It would bite the first time a
volunteer-facing component read one, which is exactly what
`inventory-tng-gnhl` has now made possible.

DERIVED FROM THE SERIALIZERS rather than listed here, so adding a field to a
`PERSONAL` tuple demands the `?` in the same change. A list here would be the
third copy of the same fact, after the serializer and the schema.
"""

import re
from pathlib import Path
from typing import Any

import pytest

from inventory.serializers import LocationSerializer, VolunteerSerializer
from inventory.tests.helpers import shipped

#: Where the client declares the shapes it reads.
TYPES = Path("frontend/src/api/types.ts")


#: The serializers that withhold, and the interface each row is read as.
# `Any` for the serializer, and deliberately: what this walk needs of it is
# `PERSONAL`, which is a plain class attribute on two unrelated serializers
# rather than anything either inherits. A Protocol describing it is not
# assignable from a class object in this checker's reading, and a cast would
# let a serializer that lacks the attribute onto the list unnoticed -- which is
# the one mistake here worth being told about, and the walk below tells you by
# raising rather than by passing.
WITHHOLDING: list[tuple[Any, str]] = [
    (VolunteerSerializer, "Volunteer"),
    (LocationSerializer, "Location"),
]


def declared(interface: str) -> str:
    """The body of one exported interface, as written."""
    found = re.search(rf"export interface {interface} \{{(.*?)^\}}", shipped(TYPES), re.S | re.M)
    assert found, f"{TYPES} declares no interface {interface}, so this test is asking about nothing"
    return found.group(1)


@pytest.mark.parametrize(
    ("serializer", "interface"),
    WITHHOLDING,
    ids=[interface for _, interface in WITHHOLDING],
)
def test_every_withheld_field_is_optional_to_the_client(serializer: Any, interface: str) -> None:
    body = declared(interface)

    required = sorted(
        field
        for field in serializer.PERSONAL
        if re.search(rf"^\s*{field}\??:", body, re.M) and f"{field}?:" not in body
    )

    assert not required, (
        f"{interface} declares {required} as always present, and {serializer.__name__} omits them for a "
        f"caller with no account: mark each optional in {TYPES}, so a reader has to handle not being "
        "told rather than reading undefined through a null check"
    )


@pytest.mark.parametrize(
    ("serializer", "interface"),
    WITHHOLDING,
    ids=[interface for _, interface in WITHHOLDING],
)
def test_the_client_declares_the_fields_this_is_about(serializer: Any, interface: str) -> None:
    """Without this, renaming a field passes the test above by asking nothing.

    The assertion there is over fields found in the interface, so a field the
    client no longer mentions is silently not checked -- and the one that
    matters is a field RENAMED on both sides, which leaves the withholding in
    place and the optionality behind.
    """
    body = declared(interface)

    absent = sorted(field for field in serializer.PERSONAL if not re.search(rf"^\s*{field}\??:", body, re.M))

    assert not absent, (
        f"{serializer.__name__} withholds {absent} and {interface} does not declare them, so nothing "
        "here checks whether the client would handle their absence"
    )
