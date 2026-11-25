"""Lines recorded with more than was moved, settled: their list, newest
first, saying how each was settled, why, by whom and when, and the links
between it, those waiting and the stock pages (Ali, 2026-11-15,
inventory-tng-73p)."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from babel.messages.catalog import Catalog
from bs4 import NavigableString
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from inventory_tng.core.local_time import show, show_day, site_zone
from inventory_tng.core.phrases import compiled, speaking
from inventory_tng.models.stock import LedgerTransaction, Movement, OverRecorded
from inventory_tng.stock import (
    open_packages,
    over_recorded,
    over_recorded_queries,
    over_recorded_routes,
)
from inventory_tng.stock.over_recorded_routes import SETTLED_VIEW, waiting_view
from inventory_tng.stock.paths import COUNTS
from inventory_tng.stock.stock_routes import recorded_more_view
from inventory_tng.web import listing
from inventory_tng.web.formatting import times

from helpers.markup import (
    Element,
    addresses,
    forms,
    links,
    read,
    sorted_columns,
    table_rows,
)
from helpers.over_recorded import PAGE
from helpers.rows import tag
from helpers.sign_in import new_admin
from helpers.stock import (
    Place,
    Spools,
    ada_id,
    count_at,
    cut,
    raise_check,
    retire,
    write,
)

# As the list says each, in English.
HOW = {how: str(words) for how, words in over_recorded_queries.SETTLED_HOW.items()}
REASONS = {why: str(words) for why, words in over_recorded.CHECKED_REASONS.items()}
TWO = str(over_recorded_queries.TWO_ADJUSTMENTS)
SEALED_ONLY = over_recorded_queries.end_only(over_recorded_queries.sealed("Spool"))
OPEN_SPOOL = over_recorded_queries.opened("Spool")

SETTLED = f"{PAGE}/settled"


def moved(session: Session, place: Place) -> OverRecorded:
    """Radios moved from the store to the van, one recorded more."""
    return raise_check(session, write(session, place))


def settled_at(check: OverRecorded) -> datetime:
    assert check.settled_at is not None
    return check.settled_at


def seen(check: OverRecorded) -> str:
    return over_recorded.seen(check)


def rows_of(page: str | Element) -> list[list[str]]:
    """The first table's rows, each cell's words."""
    return table_rows(read(page).all("table")[0])


def settled_rows(client: TestClient, path: str = SETTLED) -> list[list[str]]:
    response = client.get(path)
    assert response.status_code == 200
    return rows_of(response.text)


def test_a_correction_is_listed_with_when_who_and_where(
    admin_client: TestClient, session: Session
) -> None:
    place = Place(session)
    check = moved(session, place)
    over_recorded.correct(
        session,
        check.id,
        seen(check),
        ada_id(session),
        over_recorded.places_of(session, check.id),
    )
    session.flush()
    page = admin_client.get(SETTLED).text
    when = show(settled_at(check), site_zone(session))
    assert settled_rows(admin_client) == [
        [
            when,
            place.item.name_shown,
            "1",
            f"{place.store.name} → {place.van.name}",
            HOW["corrected"],
            TWO,
            "ada",
        ]
    ]
    assert f"/admin/stock?by=item#item-{place.item.id}" in addresses(page)
    shown = read(page)
    for spot in (place.store, place.van):
        assert (spot.name, f"{COUNTS}/{spot.id}?item={place.item.id}") in links(shown)
    # Read only: nothing to send.
    assert not shown.one("main").all("form")


def test_a_place_that_cannot_be_counted_is_not_linked(
    admin_client: TestClient, session: Session
) -> None:
    """All three radios recorded more, corrected: the store holds none
    again, and retired, may not be counted."""
    place = Place(session)
    check = raise_check(session, write(session, place), Decimal(3))
    over_recorded.correct(
        session,
        check.id,
        seen(check),
        ada_id(session),
        over_recorded.places_of(session, check.id),
    )
    retire(session, place.store)
    page = admin_client.get(SETTLED).text
    assert f"{COUNTS}/{place.store.id}" not in page
    assert f"{COUNTS}/{place.van.id}?item={place.item.id}" in page
    ((*_, where, _, _, _),) = rows_of(page)
    assert where == f"{place.store.name} → {place.van.name}"


def test_each_reason_marked_checked_says_why(
    admin_client: TestClient, session: Session
) -> None:
    place = Place(session)
    admin = ada_id(session)
    for reason in ("left", "arrived", "both"):
        check = moved(session, place)
        over_recorded.mark_checked(session, check.id, seen(check), admin, reason)
    check = moved(session, place)
    words = "Moved back by <b>hand</b> & told Alex"
    over_recorded.mark_checked(session, check.id, seen(check), admin, "other", words)
    session.flush()
    page = admin_client.get(SETTLED).text
    found = [(row[4], row[5]) for row in rows_of(page)]
    assert found == [
        (
            HOW["checked"],
            f"{REASONS['other']} {words}",
        ),
        (HOW["checked"], REASONS["both"]),
        (HOW["checked"], REASONS["arrived"]),
        (HOW["checked"], REASONS["left"]),
    ]
    # Exact markup on purpose: what was typed is escaped, never a tag.
    assert "<b>hand</b>" not in page


def whole_recorded_more(session: Session) -> tuple[Spools, OverRecorded]:
    """A spool's open package moved whole from the store to the van, one
    metre recorded more."""
    spools = Spools(session)
    made = spools.opened()
    line = cut(made, "305") | {"whole": True}
    return spools, raise_check(session, write(session, spools.place, lines=[line]))


@pytest.mark.parametrize(
    ("count", "how", "why"),
    [
        ("305", HOW["confirmed"], lambda _: over_recorded_queries.held_words("305 m")),
        ("180", HOW["count corrected"], over_recorded_queries.adjusted_at),
    ],
)
def test_a_count_of_one_moved_whole_says_if_it_was_confirmed(
    admin_client: TestClient,
    session: Session,
    count: str,
    how: str,
    why: Callable[[str], str],
) -> None:
    """Confirm count settles one as checked with no reason, or as
    corrected: told apart by what is kept."""
    spools, check = whole_recorded_more(session)
    over_recorded.confirm_count(
        session, check.id, seen(check), ada_id(session), Decimal(count)
    )
    session.flush()
    ((*_, shown_how, shown_why, _),) = settled_rows(admin_client)
    assert (shown_how, shown_why) == (how, why(spools.van.name))


def test_a_count_at_each_end_says_where_and_when(
    admin_client: TestClient, session: Session
) -> None:
    place = Place(session)
    moved(session, place)
    count_at(admin_client, place.van.id, {f"counted_{place.item.id}": "1"})
    count_at(admin_client, place.store.id, {f"counted_{place.item.id}": "2"})
    session.expire_all()
    check = session.scalars(select(OverRecorded)).one()
    assert check.settled_how == "counted"
    day = show_day(settled_at(check), site_zone(session))
    ((*_, how, why, _),) = settled_rows(admin_client)
    assert (how, why) == (
        HOW["counted"],
        ", ".join(
            (
                over_recorded_queries.counted_on(place.store.name, day),
                over_recorded_queries.counted_on(place.van.name, day),
            )
        ),
    )


def test_corrected_after_one_end_counted_names_the_other(
    admin_client: TestClient, session: Session
) -> None:
    place = Place(session)
    moved(session, place)
    count_at(admin_client, place.van.id, {f"counted_{place.item.id}": "1"})
    session.expire_all()
    check = session.scalars(select(OverRecorded)).one()
    over_recorded.correct(
        session,
        check.id,
        seen(check),
        ada_id(session),
        over_recorded.places_of(session, check.id),
    )
    session.flush()
    ((*_, how, why, _),) = settled_rows(admin_client)
    assert (how, why) == (
        HOW["corrected"],
        over_recorded_queries.end_only(place.store.name),
    )


def opening(session: Session) -> tuple[Spools, OverRecorded]:
    """A spool opened in the store, recorded more."""
    spools = Spools(session)
    spools.opened()
    transaction = session.scalars(
        select(LedgerTransaction)
        .where(LedgerTransaction.kind == "open")
        .order_by(LedgerTransaction.id.desc())
    ).first()
    return spools, raise_check(session, transaction, Decimal(305))


def test_an_opening_is_listed_at_its_place_opened(
    admin_client: TestClient, session: Session
) -> None:
    spools, check = opening(session)
    over_recorded.correct(
        session,
        check.id,
        seen(check),
        ada_id(session),
        over_recorded.places_of(session, check.id),
    )
    session.flush()
    ((_, _, amount, where, how, why, _),) = settled_rows(admin_client)
    assert (where, how, why) == (
        f"{spools.store.name} (opened)",
        HOW["corrected"],
        TWO,
    )
    assert amount == times(1, "Spool")


def test_an_openings_open_package_closed_before_correct_names_the_sealed_end(
    admin_client: TestClient, session: Session
) -> None:
    """Correct skips the end a close wrote off (inventory-tng-nyl)."""
    _, check = opening(session)
    admin = ada_id(session)
    movement = session.get_one(Movement, check.movement_id)
    assert movement.to_open_package_id is not None
    # Before Correct: in one test transaction, now() is when it began.
    at = datetime.now(UTC) - timedelta(days=1)
    assert open_packages.close(
        session, movement.to_open_package_id, admin, at, how="direct"
    )
    session.flush()
    over_recorded.correct(
        session,
        check.id,
        seen(check),
        admin,
        over_recorded.places_of(session, check.id),
    )
    session.flush()
    ((*_, how, why, _),) = settled_rows(admin_client)
    assert (how, why) == (HOW["corrected"], SEALED_ONLY)


@pytest.mark.parametrize(
    ("closed_first", "hours", "why"),
    [
        # Closed before, timed after: the close's time is the server's
        # clock as it began, and Correct's when its transaction began.
        (True, 1, SEALED_ONLY),
        # Closed after, timed before: begun before Correct, it waited for
        # the place Correct held.
        (False, -1, TWO),
    ],
)
def test_what_correct_wrote_is_told_by_what_it_wrote_not_when(
    admin_client: TestClient,
    session: Session,
    closed_first: bool,
    hours: int,
    why: str,
) -> None:
    _, check = opening(session)
    admin = ada_id(session)
    movement = session.get_one(Movement, check.movement_id)
    open_id = movement.to_open_package_id
    assert open_id is not None
    began = session.scalar(select(func.now()))
    assert began is not None
    at = began + timedelta(hours=hours)
    if closed_first:
        assert open_packages.close(session, open_id, admin, at, how="direct")
        session.flush()
    over_recorded.correct(
        session,
        check.id,
        seen(check),
        admin,
        over_recorded.places_of(session, check.id),
    )
    session.flush()
    if not closed_first:
        assert open_packages.close(session, open_id, admin, at, how="direct")
        session.flush()
    ((*_, how, shown_why, _),) = settled_rows(admin_client)
    assert (how, shown_why) == (HOW["corrected"], why)


def test_an_openings_ends_counted_name_their_forms(
    admin_client: TestClient, session: Session
) -> None:
    """A count of the sealed spools, and of the open one."""
    spools, check = opening(session)
    movement = session.get_one(Movement, check.movement_id)
    open_id = movement.to_open_package_id
    count_at(
        admin_client,
        spools.store.id,
        {
            f"sealed_{spools.cable.id}_{spools.spool.id}": "1",
            f"opened_{open_id}": "305",
        },
    )
    session.expire_all()
    check = session.get_one(OverRecorded, check.id)
    assert check.settled_how == "counted"
    day = show_day(settled_at(check), site_zone(session))
    ((*_, how, why, _),) = settled_rows(admin_client)
    assert (how, why) == (
        HOW["counted"],
        ", ".join(
            (
                over_recorded_queries.counted_on(
                    over_recorded_queries.sealed("Spool"), day
                ),
                over_recorded_queries.within(
                    over_recorded_queries.counted_on(OPEN_SPOOL, day)
                ),
            )
        ),
    )


def test_newest_first_in_pages_of_one_item_or_all(
    admin_client: TestClient, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(listing, "PER_PAGE", 2)
    place, other = Place(session), Place(session)
    admin = ada_id(session)
    start = datetime(2026, 11, 14, 12, tzinfo=UTC)
    for n, at in enumerate((place, other, place, place)):
        check = moved(session, at)
        over_recorded.mark_checked(session, check.id, seen(check), admin, "both")
        session.flush()
        check.settled_at = start + timedelta(hours=n)
    session.flush()
    zone = site_zone(session)
    times = [show(start + timedelta(hours=n), zone) for n in range(4)]
    first = settled_rows(admin_client)
    assert [row[0] for row in first] == [times[3], times[2]]
    page = admin_client.get(f"{SETTLED}?page=2").text
    assert [row[0] for row in rows_of(page)] == [times[1], times[0]]
    assert read(page).all("a", rel="prev")
    assert "Page 2 of 2" in page
    # A page past the last is the last; one not a number, the first.
    assert settled_rows(admin_client, f"{SETTLED}?page=9")[0][0] == times[1]
    assert settled_rows(admin_client, f"{SETTLED}?page=x")[0][0] == times[3]
    # One item's, its pages keeping it.
    only = f"{SETTLED}?item={place.item.id}"
    page = admin_client.get(only).text
    assert [row[0] for row in rows_of(page)] == [times[3], times[2]]
    assert f"{SETTLED}?item={place.item.id}&page=2" in addresses(page)
    assert f"Only {place.item.name_shown}." in page
    second = rows_of(admin_client.get(f"{only}&page=2").text)
    assert [row[0] for row in second] == [times[0]]
    assert {row[1] for row in [*rows_of(page), *second]} == {place.item.name_shown}


def test_nothing_settled_says_so(admin_client: TestClient) -> None:
    assert settled_rows(admin_client) == [["Nothing settled yet."]]


def views_of(page: str, label: str) -> list[tuple[str, str, str | None]]:
    """What a nav holds, in order: each part's tag (text for words between
    them), words and link. A comment is not a part."""
    nav = read(page).one("nav", aria_label=label)
    return [
        (part.name, part.text, part.attrs.get("href"))
        if isinstance(part, Element)
        else ("text", str(part).strip(), None)
        for part in nav.contents
        if isinstance(part, Element) or (type(part) is NavigableString and part.strip())
    ]


@pytest.mark.parametrize(
    "path",
    [
        "/admin/stock",
        "/admin/stock?by=item",
        "/admin/stock/reorder",
        "/admin/stock/needs-counting",
    ],
)
def test_the_stock_views_link_to_those_waiting_and_settled_always(
    admin_client: TestClient, session: Session, path: str
) -> None:
    """Even with none waiting."""
    page = admin_client.get(path).text
    nav = views_of(page, "Stock views")
    assert ("a", recorded_more_view(0), PAGE) in nav
    assert ("a", SETTLED_VIEW, SETTLED) in nav
    place = Place(session)
    moved(session, place)
    moved(session, place)
    nav = views_of(admin_client.get(path).text, "Stock views")
    assert ("a", recorded_more_view(2), PAGE) in nav


def test_waiting_and_settled_link_to_each_other_keeping_the_item(
    admin_client: TestClient, session: Session
) -> None:
    place, other = Place(session), Place(session)
    moved(session, place)
    moved(session, other)
    label = str(over_recorded_routes.TITLE)
    nav = views_of(admin_client.get(PAGE).text, label)
    assert nav == [("strong", waiting_view(2), None), ("a", SETTLED_VIEW, SETTLED)]
    nav = views_of(admin_client.get(SETTLED).text, label)
    assert nav == [("a", waiting_view(2), PAGE), ("strong", SETTLED_VIEW, None)]
    only = f"?item={place.item.id}"
    nav = views_of(admin_client.get(f"{PAGE}{only}").text, label)
    assert nav == [
        ("strong", waiting_view(1), None),
        ("a", SETTLED_VIEW, f"{SETTLED}{only}"),
    ]
    nav = views_of(admin_client.get(f"{SETTLED}{only}").text, label)
    assert nav == [
        ("a", waiting_view(1), f"{PAGE}{only}"),
        ("strong", SETTLED_VIEW, None),
    ]
    # None waiting, still linked.
    for check in session.scalars(select(OverRecorded)):
        over_recorded.mark_checked(
            session, check.id, seen(check), ada_id(session), "both"
        )
    session.flush()
    nav = views_of(admin_client.get(SETTLED).text, label)
    assert ("a", waiting_view(0), PAGE) in nav


def test_settled_by_another_administrator_names_them(
    admin_client: TestClient, session: Session
) -> None:
    other, _ = new_admin(session, f"bo-{tag()}", totp=False)
    check = moved(session, Place(session))
    over_recorded.mark_checked(session, check.id, seen(check), other.id, "left")
    session.flush()
    ((*_, by),) = settled_rows(admin_client)
    assert by == other.username


def test_an_openings_sealed_end_counted_then_closed_says_so(
    admin_client: TestClient, session: Session
) -> None:
    """The close puts the open end right, though no count did
    (inventory-tng-die)."""
    spools, check = opening(session)
    movement = session.get_one(Movement, check.movement_id)
    assert movement.to_open_package_id is not None
    count_at(
        admin_client,
        spools.store.id,
        {f"sealed_{spools.cable.id}_{spools.spool.id}": "1"},
    )
    at = datetime(2026, 11, 20, 15, tzinfo=UTC)
    closed = open_packages.close(
        session, movement.to_open_package_id, ada_id(session), at, how="direct"
    )
    assert closed
    session.flush()
    session.expire_all()
    check = session.get_one(OverRecorded, check.id)
    assert (check.settled_how, check.arrived_count_id) == ("counted", None)
    zone = site_zone(session)
    assert check.left_count_id is not None
    counted_on = session.get_one(LedgerTransaction, check.left_count_id).created_at
    ((*_, how, why, _),) = settled_rows(admin_client)
    sealed, opened = show_day(counted_on, zone), show_day(at, zone)
    assert (how, why) == (
        HOW["counted"],
        ", ".join(
            (
                over_recorded_queries.counted_on(
                    over_recorded_queries.sealed("Spool"), sealed
                ),
                over_recorded_queries.within(
                    over_recorded_queries.closed_on_day(OPEN_SPOOL, opened)
                ),
            )
        ),
    )


def test_both_ends_right_then_closed_names_the_count_and_the_close(
    admin_client: TestClient, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Its sealed end counted, its open package closed by a close that
    skipped the check (a count held it), then Close it: counted, by the
    count and the close (inventory-tng-dro)."""
    spools, check = opening(session)
    movement = session.get_one(Movement, check.movement_id)
    assert movement.to_open_package_id is not None
    count_at(
        admin_client,
        spools.store.id,
        {f"sealed_{spools.cable.id}_{spools.spool.id}": "1"},
    )
    at = datetime(2026, 11, 20, 15, tzinfo=UTC)
    with monkeypatch.context() as patched:
        patched.setattr(over_recorded, "open_package_closed", lambda *_: None)
        closed = open_packages.close(
            session, movement.to_open_package_id, ada_id(session), at, how="direct"
        )
    assert closed
    session.flush()
    session.expire_all()
    check = session.get_one(OverRecorded, check.id)
    over_recorded.close(session, check.id, seen(check), ada_id(session))
    session.flush()
    assert check.left_count_id is not None
    counted_on = session.get_one(LedgerTransaction, check.left_count_id).created_at
    zone = site_zone(session)
    sealed, opened = show_day(counted_on, zone), show_day(at, zone)
    ((*_, how, why, by),) = settled_rows(admin_client)
    assert (how, why, by) == (
        HOW["counted"],
        ", ".join(
            (
                over_recorded_queries.counted_on(
                    over_recorded_queries.sealed("Spool"), sealed
                ),
                over_recorded_queries.within(
                    over_recorded_queries.closed_on_day(OPEN_SPOOL, opened)
                ),
            )
        ),
        "ada",
    )


def test_one_end_only_counted_names_that_end(
    admin_client: TestClient, session: Session
) -> None:
    """An install has no place it arrives at; an open package moved whole
    has one end only, where it arrived."""
    place = Place(session)
    install = write(session, place, kind="install", to_location_id=None)
    used = raise_check(session, install)
    count_at(admin_client, place.store.id, {f"counted_{place.item.id}": "0"})
    spools, check = whole_recorded_more(session)
    movement = session.get_one(Movement, check.movement_id)
    count_at(
        admin_client, spools.van.id, {f"opened_{movement.to_open_package_id}": "1"}
    )
    session.expire_all()
    zone = site_zone(session)

    def counted_on(count_id: int | None) -> str:
        """The day of the count that put an end right, as the row says it."""
        assert count_id is not None
        return show_day(session.get_one(LedgerTransaction, count_id).created_at, zone)

    used = session.get_one(OverRecorded, used.id)
    check = session.get_one(OverRecorded, check.id)
    found = [(row[3], row[4], row[5]) for row in settled_rows(admin_client)]
    assert found == [
        (
            f"{spools.store.name} → {spools.van.name}",
            HOW["counted"],
            over_recorded_queries.counted_on(
                spools.van.name, counted_on(check.arrived_count_id)
            ),
        ),
        (
            f"from {place.store.name}",
            HOW["counted"],
            over_recorded_queries.counted_on(
                place.store.name, counted_on(used.left_count_id)
            ),
        ),
    ]


def test_every_column_sorts_either_way(
    admin_client: TestClient, session: Session
) -> None:
    """Newest first, unless a heading sorts them: pressed, the way it
    sorts first; pressed again, the other way (Ali, 2026-11-15,
    inventory-tng-73p, inventory-tng-qwb)."""
    zed, _ = new_admin(session, f"zed{tag()}")
    start = datetime(2026, 11, 14, 12, tzinfo=UTC)
    places = [Place(session) for _ in range(3)]
    for n, place in enumerate(places):
        check = raise_check(session, write(session, place), Decimal(n + 1))
        if n == 1:
            over_recorded.correct(
                session,
                check.id,
                seen(check),
                ada_id(session),
                over_recorded.places_of(session, check.id),
            )
        else:
            over_recorded.mark_checked(
                session, check.id, seen(check), ada_id(session), "both"
            )
        session.flush()
        check.settled_at = start + timedelta(hours=n)
        if n == 2:
            check.settled_by_admin_id = zed.id
    session.flush()
    mine = {place.item.name_shown for place in places}

    def shown(query: str = "") -> list[list[str]]:
        rows = settled_rows(admin_client, f"{SETTLED}?per=all{query}")
        return [row for row in rows if row[1] in mine]

    newest = shown()
    assert [row[2] for row in newest] == ["3", "2", "1"]
    assert shown("&sort=settled") == newest
    assert shown("&sort=settled&order=asc") == newest[::-1]
    for column, cell in (
        ("item", 1),
        ("amount", 2),
        ("where", 3),
        ("how", 4),
        # Why by its words as shown (Ali, 2026-11-15, inventory-tng-73p).
        ("why", 5),
        ("by", 6),
    ):
        first = [row[cell].lower() for row in shown(f"&sort={column}")]
        assert first == sorted(row[cell].lower() for row in newest), column
        last = [row[cell].lower() for row in shown(f"&sort={column}&order=desc")]
        assert last == first[::-1], column
    page = admin_client.get(f"{SETTLED}?sort=item").text
    assert sorted_columns(page) == [
        ("ascending", "Item", f"{SETTLED}?sort=item&order=desc")
    ]
    assert f"{SETTLED}?sort=why" in addresses(page)
    # A sort not offered is newest first.
    assert shown("&sort=whom") == newest


def test_how_sorts_by_its_words_in_the_language_spoken(
    admin_client: TestClient, session: Session
) -> None:
    """How is sorted by its words as the list says them, in the language
    spoken when the list is asked for, not English."""
    places = [Place(session) for _ in range(2)]
    first = raise_check(session, write(session, places[0]))
    over_recorded.correct(
        session,
        first.id,
        seen(first),
        ada_id(session),
        over_recorded.places_of(session, first.id),
    )
    second = raise_check(session, write(session, places[1]))
    over_recorded.mark_checked(
        session, second.id, seen(second), ada_id(session), "both"
    )
    session.flush()
    mine = {place.item.name_shown for place in places}
    # English sorts Checked before Corrected; this language the other way.
    catalogue = Catalog()
    catalogue.add(HOW["checked"], "Zu prüfen")
    catalogue.add(HOW["corrected"], "Angepasst")
    with speaking(compiled(catalogue)):
        rows = settled_rows(admin_client, f"{SETTLED}?per=all&sort=how")
    assert [row[4] for row in rows if row[1] in mine] == ["Angepasst", "Zu prüfen"]


def test_rows_per_page_25_50_100_or_all(
    admin_client: TestClient, session: Session
) -> None:
    place = Place(session)
    admin = ada_id(session)
    for _ in range(26):
        check = moved(session, place)
        over_recorded.mark_checked(session, check.id, seen(check), admin, "both")
        session.flush()
    only = f"{SETTLED}?item={place.item.id}"
    page = read(admin_client.get(only).text)
    assert len(rows_of(page)) == 26
    assert ("25", f"{only}&per=25") in links(page)
    assert read(page).one("a", "50", aria_current="page").attrs["href"] == only
    assert ("All", f"{only}&per=all") in links(page)
    page = admin_client.get(f"{only}&per=25").text
    assert len(rows_of(page)) == 25
    assert "Page 1 of 2" in page
    assert f"{only}&per=25&page=2" in addresses(page)
    assert len(settled_rows(admin_client, f"{only}&per=25&page=2")) == 1
    # Sorted by Why, worded in Python, in pages too.
    assert len(settled_rows(admin_client, f"{only}&per=25&sort=why&page=2")) == 1
    assert len(settled_rows(admin_client, f"{only}&per=all")) == 26
    # A size not offered is 50.
    assert len(settled_rows(admin_client, f"{only}&per=7")) == 26


def test_those_waiting_sort_by_each_heading_and_come_in_pages(
    admin_client: TestClient, session: Session
) -> None:
    """By item unless a heading sorts them; pressed again, the other way;
    in pages of 25, 50, 100 or all (Ali, 2026-11-15, inventory-tng-qwb)."""
    places = [Place(session) for _ in range(3)]
    for n, place in enumerate(places):
        raise_check(session, write(session, place), Decimal(3 - n))
    session.flush()
    mine = {place.item.name_shown for place in places}

    def items(query: str = "") -> list[str]:
        rows = rows_of(admin_client.get(f"{PAGE}?per=all{query}").text)
        return [row[0] for row in rows if row[0] in mine]

    by_name = sorted(mine, key=str.lower)
    assert items() == by_name
    assert items("&sort=item&order=desc") == by_name[::-1]
    names = [place.item.name_shown for place in places]
    # Recorded more: 3, 2, 1.
    assert items("&sort=amount") == names[::-1]
    assert items("&sort=amount&order=desc") == names
    # The newest transaction first.
    assert items("&sort=transaction") == names[::-1]
    assert items("&sort=transaction&order=asc") == names
    where = sorted(names, key=lambda n: places[names.index(n)].store.name.lower())
    assert items("&sort=where") == where
    page = read(admin_client.get(PAGE).text)
    assert ("Recorded more", f"{PAGE}?sort=amount") in links(page)
    assert ("25", f"{PAGE}?per=25") in links(page)
    only = f"{PAGE}?item={places[0].item.id}"
    sorted_only = admin_client.get(f"{only}&sort=amount").text
    assert f"{only}&sort=amount&order=desc" in addresses(sorted_only)
    assert ("Show all", f"{PAGE}?sort=amount") in links(sorted_only)
    for _ in range(25):
        moved(session, places[0])
    assert len(rows_of(admin_client.get(f"{only}&per=25").text)) == 25
    assert len(rows_of(admin_client.get(f"{only}&per=25&page=2").text)) == 1


def test_an_answer_comes_back_to_those_waiting_as_they_were(
    admin_client: TestClient, session: Session
) -> None:
    """Their sort, page size, page and item go with each answer, and come
    back, or are drawn again with a problem; a page past the end, as after
    the last one on it is answered, shows the last there is
    (inventory-tng-qwb)."""
    place = Place(session)
    check, other = moved(session, place), moved(session, place)
    view = f"item={place.item.id}&sort=amount&order=desc&per=25&page=2"
    # Drawn, the page is 1 of 1: a page past the end shows the last.
    shown = view.removesuffix("&page=2")
    page = admin_client.get(f"{PAGE}?{view}").text
    assert ("post", f"{PAGE}/{check.id}/checked?{shown}") in forms(page)
    assert ("post", f"{PAGE}/{check.id}/correct?{shown}") in forms(page)
    asked = admin_client.post(
        f"{PAGE}/{check.id}/checked?{view}", data={"seen": seen(check)}
    )
    assert asked.status_code == 400
    assert ("post", f"{PAGE}/{check.id}/checked?{shown}") in forms(asked.text)
    assert [way for way, _, _ in sorted_columns(asked.text)] == ["descending"]
    done = admin_client.post(
        f"{PAGE}/{check.id}/checked?{view}",
        data={"seen": seen(check), "reason": "both"},
    )
    back = done.headers["location"]
    assert back == f"{PAGE}?{view}&notice=over_recorded_checked"
    # Page 2 of 25 a page is past the end: the last page, the first.
    answers = forms(admin_client.get(back).text)
    assert any(to.startswith(f"{PAGE}/{other.id}/checked?") for _, to in answers)
