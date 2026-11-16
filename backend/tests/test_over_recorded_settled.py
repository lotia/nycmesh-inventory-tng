"""Lines recorded with more than was moved, settled: their list, newest
first, saying how each was settled, why, by whom and when, and the links
between it, those waiting and the stock pages (Ali, 2026-11-15,
inventory-tng-73p)."""

import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from babel.messages.catalog import Catalog
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from inventory_tng import open_packages, over_recorded
from inventory_tng.admin import listing
from inventory_tng.admin import over_recorded as over_recorded_page
from inventory_tng.admin.common import SETTLED_VIEW
from inventory_tng.admin.counts import COUNTS
from inventory_tng.admin.over_recorded import waiting_view
from inventory_tng.admin.stock import recorded_more_view
from inventory_tng.local_time import show, show_day, site_zone
from inventory_tng.models import LedgerTransaction, Movement, OverRecorded
from inventory_tng.phrases import compiled, speaking

from helpers.rows import tag
from helpers.sign_in import new_admin
from helpers.stock import Place, Spools, ada_id, cut, raised, retire, write

# As the list says each, in English.
HOW = {how: str(words) for how, words in over_recorded.SETTLED_HOW.items()}
REASONS = {why: str(words) for why, words in over_recorded.CHECKED_REASONS.items()}
TWO = str(over_recorded.TWO_ADJUSTMENTS)
SEALED_ONLY = over_recorded.end_only(over_recorded.sealed("Spool"))
OPEN_SPOOL = over_recorded.opened("Spool")

WAITING = "/admin/stock/recorded-more"
SETTLED = f"{WAITING}/settled"


def moved(session: Session, place: Place) -> OverRecorded:
    """Radios moved from the store to the van, one recorded more."""
    return raised(session, write(session, place))


def settled_at(check: OverRecorded) -> datetime:
    assert check.settled_at is not None
    return check.settled_at


def seen(check: OverRecorded) -> str:
    return over_recorded.seen(check)


def rows_of(page: str) -> list[list[str]]:
    """The settled table's rows, each cell's words, tags taken out."""
    body = page.split("<table>", 1)[1].split("</table>", 1)[0]
    return [
        [" ".join(re.sub(r"<[^>]+>", " ", cell).split()) for cell in cells]
        for row in re.findall(r"<tr>(.*?)</tr>", body, re.DOTALL)
        if (cells := re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL))
    ]


def settled_rows(client: TestClient, path: str = SETTLED) -> list[list[str]]:
    response = client.get(path)
    assert response.status_code == 200
    return rows_of(response.text)


def count_at(client: TestClient, place_id: int, lines: dict[str, str]) -> None:
    """A count of a place sent with `lines`, from its form as shown now."""
    page = client.get(f"{COUNTS}/{place_id}").text
    form = dict(
        re.findall(r'type="hidden" name="(key|as_of|items)" value="([^"]*)"', page)
    )
    assert client.post(f"{COUNTS}/{place_id}", data=form | lines).status_code == 303


def test_a_correction_is_listed_with_when_who_and_where(
    admin_client: TestClient, session: Session
) -> None:
    place = Place(session)
    check = moved(session, place)
    over_recorded.correct(session, check.id, seen(check), ada_id(session))
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
    assert f'href="/admin/stock?by=item#item-{place.item.id}"' in page
    for spot in (place.store, place.van):
        link = f'<a href="{COUNTS}/{spot.id}?item={place.item.id}">{spot.name}</a>'
        assert link in page
    # Read only: nothing to send.
    assert "<form" not in page.split("<main", 1)[1]


def test_a_place_that_cannot_be_counted_is_not_linked(
    admin_client: TestClient, session: Session
) -> None:
    """All three radios recorded more, corrected: the store holds none
    again, and retired, may not be counted."""
    place = Place(session)
    check = raised(session, write(session, place), Decimal(3))
    over_recorded.correct(session, check.id, seen(check), ada_id(session))
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
            f"{REASONS['other']} Moved back by &lt;b&gt;hand&lt;/b&gt; &amp; told Alex",
        ),
        (HOW["checked"], REASONS["both"]),
        (HOW["checked"], REASONS["arrived"]),
        (HOW["checked"], REASONS["left"]),
    ]
    assert "<b>hand</b>" not in page


def moved_whole(session: Session) -> tuple[Spools, OverRecorded]:
    """A spool's open package moved whole from the store to the van, one
    metre recorded more."""
    spools = Spools(session)
    made = spools.opened()
    line = cut(made, "305") | {"whole": True}
    return spools, raised(session, write(session, spools.place, lines=[line]))


@pytest.mark.parametrize(
    ("count", "how", "why"),
    [
        ("305", HOW["confirmed"], lambda _: over_recorded.held_words("305 m")),
        ("180", HOW["count corrected"], over_recorded.adjusted_at),
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
    spools, check = moved_whole(session)
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
                over_recorded.counted_on(place.store.name, day),
                over_recorded.counted_on(place.van.name, day),
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
    over_recorded.correct(session, check.id, seen(check), ada_id(session))
    session.flush()
    ((*_, how, why, _),) = settled_rows(admin_client)
    assert (how, why) == (HOW["corrected"], over_recorded.end_only(place.store.name))


def opening(session: Session) -> tuple[Spools, OverRecorded]:
    """A spool opened in the store, recorded more."""
    spools = Spools(session)
    spools.opened()
    transaction = session.scalars(
        select(LedgerTransaction)
        .where(LedgerTransaction.kind == "open")
        .order_by(LedgerTransaction.id.desc())
    ).first()
    return spools, raised(session, transaction, Decimal(305))


def test_an_opening_is_listed_at_its_place_opened(
    admin_client: TestClient, session: Session
) -> None:
    spools, check = opening(session)
    over_recorded.correct(session, check.id, seen(check), ada_id(session))
    session.flush()
    ((_, _, amount, where, how, why, _),) = settled_rows(admin_client)
    assert (where, how, why) == (
        f"{spools.store.name} (opened)",
        HOW["corrected"],
        TWO,
    )
    assert amount == over_recorded.times(1, "Spool")


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
    assert open_packages.close(session, movement.to_open_package_id, admin, at)
    session.flush()
    over_recorded.correct(session, check.id, seen(check), admin)
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
        assert open_packages.close(session, open_id, admin, at)
        session.flush()
    over_recorded.correct(session, check.id, seen(check), admin)
    session.flush()
    if not closed_first:
        assert open_packages.close(session, open_id, admin, at)
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
                over_recorded.counted_on(over_recorded.sealed("Spool"), day),
                over_recorded.within(over_recorded.counted_on(OPEN_SPOOL, day)),
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
    assert 'rel="prev"' in page
    assert "Page 2 of 2" in page
    # A page past the last is the last; one not a number, the first.
    assert settled_rows(admin_client, f"{SETTLED}?page=9")[0][0] == times[1]
    assert settled_rows(admin_client, f"{SETTLED}?page=x")[0][0] == times[3]
    # One item's, its pages keeping it.
    only = f"{SETTLED}?item={place.item.id}"
    page = admin_client.get(only).text
    assert [row[0] for row in rows_of(page)] == [times[3], times[2]]
    assert f'href="{SETTLED}?item={place.item.id}&amp;page=2"' in page
    assert f"Only {place.item.name_shown}." in page
    second = rows_of(admin_client.get(f"{only}&page=2").text)
    assert [row[0] for row in second] == [times[0]]
    assert {row[1] for row in [*rows_of(page), *second]} == {place.item.name_shown}


def test_nothing_settled_says_so(admin_client: TestClient, session: Session) -> None:
    assert settled_rows(admin_client) == [["Nothing settled yet."]]


def views_of(page: str, label: str) -> str:
    """A nav's words and links, as one line."""
    nav = re.search(rf'<nav aria-label="{label}"[^>]*>(.*?)</nav>', page, re.DOTALL)
    assert nav is not None, label
    return " ".join(nav.group(1).split())


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
    assert f'<a href="{WAITING}">{recorded_more_view(0)}</a>' in nav
    assert f'<a href="{SETTLED}">{SETTLED_VIEW}</a>' in nav
    place = Place(session)
    moved(session, place)
    moved(session, place)
    nav = views_of(admin_client.get(path).text, "Stock views")
    assert f'<a href="{WAITING}">{recorded_more_view(2)}</a>' in nav


def test_waiting_and_settled_link_to_each_other_keeping_the_item(
    admin_client: TestClient, session: Session
) -> None:
    place, other = Place(session), Place(session)
    moved(session, place)
    moved(session, other)
    label = str(over_recorded_page.TITLE)
    nav = views_of(admin_client.get(WAITING).text, label)
    assert nav == (
        f'<strong>{waiting_view(2)}</strong> <a href="{SETTLED}">{SETTLED_VIEW}</a>'
    )
    nav = views_of(admin_client.get(SETTLED).text, label)
    assert nav == (
        f'<a href="{WAITING}">{waiting_view(2)}</a> <strong>{SETTLED_VIEW}</strong>'
    )
    only = f"?item={place.item.id}"
    nav = views_of(admin_client.get(f"{WAITING}{only}").text, label)
    assert nav == (
        f"<strong>{waiting_view(1)}</strong> "
        f'<a href="{SETTLED}{only}">{SETTLED_VIEW}</a>'
    )
    nav = views_of(admin_client.get(f"{SETTLED}{only}").text, label)
    assert nav == (
        f'<a href="{WAITING}{only}">{waiting_view(1)}</a> '
        f"<strong>{SETTLED_VIEW}</strong>"
    )
    # None waiting, still linked.
    for check in session.scalars(select(OverRecorded)):
        over_recorded.mark_checked(
            session, check.id, seen(check), ada_id(session), "both"
        )
    session.flush()
    nav = views_of(admin_client.get(SETTLED).text, label)
    assert f'<a href="{WAITING}">{waiting_view(0)}</a>' in nav


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
        session, movement.to_open_package_id, ada_id(session), at
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
                over_recorded.counted_on(over_recorded.sealed("Spool"), sealed),
                over_recorded.within(over_recorded.closed_on_day(OPEN_SPOOL, opened)),
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
        patched.setattr(over_recorded, "open_package_closed", lambda *_: 0)
        closed = open_packages.close(
            session, movement.to_open_package_id, ada_id(session), at
        )
    assert closed == open_packages.Closed(False, 0)
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
                over_recorded.counted_on(over_recorded.sealed("Spool"), sealed),
                over_recorded.within(over_recorded.closed_on_day(OPEN_SPOOL, opened)),
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
    used = raised(session, install)
    count_at(admin_client, place.store.id, {f"counted_{place.item.id}": "0"})
    spools, check = moved_whole(session)
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
            over_recorded.counted_on(
                spools.van.name, counted_on(check.arrived_count_id)
            ),
        ),
        (
            f"from {place.store.name}",
            HOW["counted"],
            over_recorded.counted_on(place.store.name, counted_on(used.left_count_id)),
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
        check = raised(session, write(session, place), Decimal(n + 1))
        if n == 1:
            over_recorded.correct(session, check.id, seen(check), ada_id(session))
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
    assert '<th aria-sort="ascending"><a href="' in page
    assert f'href="{SETTLED}?sort=item&amp;order=desc"' in page
    assert f'href="{SETTLED}?sort=why"' in page
    # A sort not offered is newest first.
    assert shown("&sort=whom") == newest


def test_how_sorts_by_its_words_in_the_language_spoken(
    admin_client: TestClient, session: Session
) -> None:
    """How is sorted by its words as the list says them, in the language
    spoken when the list is asked for, not English."""
    places = [Place(session) for _ in range(2)]
    first = raised(session, write(session, places[0]))
    over_recorded.correct(session, first.id, seen(first), ada_id(session))
    second = raised(session, write(session, places[1]))
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
    page = admin_client.get(only).text
    assert len(rows_of(page)) == 26
    assert f'<a href="{only}&amp;per=25">25</a>' in page
    assert '<a href="' + f'{only}" aria-current="page">50</a>' in page
    assert f'<a href="{only}&amp;per=all">All</a>' in page
    page = admin_client.get(f"{only}&per=25").text
    assert len(rows_of(page)) == 25
    assert "Page 1 of 2" in page
    assert f'href="{only}&amp;per=25&amp;page=2"' in page
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
        raised(session, write(session, place), Decimal(3 - n))
    session.flush()
    mine = {place.item.name_shown for place in places}

    def items(query: str = "") -> list[str]:
        rows = rows_of(admin_client.get(f"{WAITING}?per=all{query}").text)
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
    page = admin_client.get(WAITING).text
    assert f'<a href="{WAITING}?sort=amount">Recorded more</a>' in page
    assert f'<a href="{WAITING}?per=25">25</a>' in page
    only = f"{WAITING}?item={places[0].item.id}"
    sorted_only = admin_client.get(f"{only}&sort=amount").text
    assert f'href="{only}&amp;sort=amount&amp;order=desc"' in sorted_only
    assert f'<a href="{WAITING}?sort=amount">Show all</a>' in sorted_only
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
    shown = view.removesuffix("&page=2").replace("&", "&amp;")
    page = admin_client.get(f"{WAITING}?{view}").text
    assert f'action="{WAITING}/{check.id}/checked?{shown}"' in page
    assert f'action="{WAITING}/{check.id}/correct?{shown}"' in page
    asked = admin_client.post(
        f"{WAITING}/{check.id}/checked?{view}", data={"seen": seen(check)}
    )
    assert asked.status_code == 400
    assert f'action="{WAITING}/{check.id}/checked?{shown}"' in asked.text
    assert 'aria-sort="descending"' in asked.text
    done = admin_client.post(
        f"{WAITING}/{check.id}/checked?{view}",
        data={"seen": seen(check), "reason": "both"},
    )
    back = done.headers["location"]
    assert back == f"{WAITING}?{view}&notice=over_recorded_checked"
    # Page 2 of 25 a page is past the end: the last page, the first.
    assert f'action="{WAITING}/{other.id}/checked?' in admin_client.get(back).text
