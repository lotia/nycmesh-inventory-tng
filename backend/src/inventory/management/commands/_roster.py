"""A pick-list of invented people, shaped like the one the consultation argues about.

`seed_demo_data` writes two volunteers, which is enough to show that a
transaction is attributed to somebody and not enough to show anything else. The
demonstration behind `inventory-tng-81f7` rests on figures measured against a
roster of eighty-six: how many names a single letter returns, how many people
share a display name, how many carry no address at all. None of that can be
shown against two rows, and the run of show asked a presenter to invent a method
minutes before a meeting. `inventory-tng-w2f6`.

EVERY PERSON HERE IS FICTION, and that is the whole safety argument rather than
a disclaimer. The run puts the pick-list on a shared screen and the call may be
recorded, so a roster of real volunteers on that screen would cause the harm the
consultation was convened to prevent. `inventory_tng.roster` says why
`PUBLIC_VOLUNTEER_DETAILS` is the safety net and not the control: seeding
fiction is what stops the accident, and the setting is what limits it.

Addresses are all `.invalid`, which is reserved by RFC 2606 and cannot resolve,
so nothing here can reach a real inbox even by accident.

DETERMINISTIC, and that is load-bearing rather than tidy. The run of show tells
a presenter to "search for the shared name you seeded" -- which is only an
instruction if the same names come out every time. Nothing here is random, so
the guide can name people and a rehearsal is worth doing.

## The three properties the demonstration needs

Held by `inventory/tests/test_demo_roster.py` rather than left to inspection,
because a change to the name pool could quietly cost any of them:

1. **Two pairs share a display name.** This is the case the pick-list cannot
   resolve on its own, and the reason a second line exists at all.
2. **One of those pairs shares a mail provider and a first initial**, so that
   the two addresses blur to the same string. That is the two-Seans example,
   and it is what makes the measured argument about masking visible rather
   than described.
3. **About 45% carry no address**, matching the real proportion. Those people
   are already indistinguishable when they share a name, which the run of show
   has to say out loud rather than be caught by.
"""

from dataclasses import dataclass

#: How many DISTINCT display names the figures assume -- "a single letter
#: returns 54 of 86" counts names, not people. Two of them are worn by two
#: people each, so the row count is a little higher. Measured against the
#: workbook, not chosen.
NAMES = 86

#: Where invented addresses live. `.invalid` is reserved by RFC 2606 and never
#: resolves, so one of these cannot reach anybody however it escapes.
PROVIDER = "mailbox.invalid"
OTHER_PROVIDER = "post.invalid"


@dataclass(frozen=True)
class Person:
    """One invented volunteer, and the address they did or did not give."""

    display_name: str
    email: str | None


#: Enough given names and surnames to build a roster with no accidental
#: collisions, so that every collision below is one somebody chose. Ordinary
#: names on purpose: a roster of obvious jokes would not read as a pick-list,
#: and the argument is about how a real one behaves.
GIVEN = (
    "Priya",
    "Marcus",
    "Dee",
    "Ruth",
    "Oscar",
    "Nadia",
    "Theo",
    "Amara",
    "Felix",
    "Ingrid",
    "Yusuf",
    "Clara",
    "Hugo",
    "Mei",
    "Rafael",
    "Sofia",
    "Jonah",
    "Bianca",
    "Omar",
    "Elena",
    "Cyrus",
    "Nora",
    "Idris",
    "Petra",
)
FAMILY = (
    "Raman",
    "Okonkwo",
    "Alvarez",
    "Whitfield",
    "Nakamura",
    "Brennan",
    "Costa",
    "Lindqvist",
    "Haddad",
    "Moreau",
    "Petrov",
    "Ellery",
)


def _blend(index: int) -> str:
    """A name from the two pools, walked so that no pair repeats."""
    given = GIVEN[index % len(GIVEN)]
    family = FAMILY[(index // len(GIVEN) + index) % len(FAMILY)]
    return f"{given} {family}"


#: The collisions, written out rather than generated, because each exists for a
#: different reason and a reader should be able to see which.
#:
#: The Delaney pair is the one the argument turns on: same display name, same
#: first initial, same provider, so masking either address produces the same
#: string and separates nobody. The Okonkwo pair shares a name and nothing
#: else, which is the ordinary case -- typing an address still tells them apart.
COLLIDING = (
    Person("Sean Delaney", f"s.delaney@{PROVIDER}"),
    Person("Sean Delaney", f"s.doyle@{PROVIDER}"),
    Person("Ada Okonkwo", f"ada.okonkwo@{OTHER_PROVIDER}"),
    Person("Ada Okonkwo", None),
)

#: The measured proportion carrying no address at all, as a fraction rather
#: than a rate, so the arithmetic is visible: nine in every twenty is 45%. A
#: fixed pattern rather than a random draw, for the reason the header gives
#: about a presenter being able to rehearse.
WITHOUT_ADDRESS, IN_EVERY = 9, 20


def roster(names: int = NAMES) -> tuple[Person, ...]:
    """The invented pick-list, the same every time it is asked for.

    `names` is how many DISTINCT display names to produce; the two shared ones
    each add a second person, so the result is longer than that by two.

    The colliding four are placed first, so that a smaller roster -- a test
    asking for a dozen names -- still carries the cases the demonstration
    needs rather than losing them off the end.
    """
    people = list(COLLIDING)
    distinct = {person.display_name for person in people}

    index = 0
    while len(distinct) < names:
        name = _blend(index)
        index += 1
        if name in distinct:
            continue
        distinct.add(name)
        given, family = name.split(" ", 1)
        without = len(people) % IN_EVERY < WITHOUT_ADDRESS
        address = None if without else f"{given.lower()}.{family.lower()}@{OTHER_PROVIDER}"
        people.append(Person(name, address))
    return tuple(people)
