import re
from datetime import UTC, datetime
from functools import partial

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, text

from inventory_tng.models import FOLDED_WITH, fold, unit_key
from inventory_tng.names import IGNORED, name_key

from helpers.sign_in import TOTP_KEYS


def test_migrations_match_the_models(alembic_config: Config) -> None:
    command.check(alembic_config)


def test_migrations_go_down_and_back_up(alembic_config: Config) -> None:
    command.downgrade(alembic_config, "base")
    command.upgrade(alembic_config, "head")


def test_short_recovery_codes_are_voided(alembic_config: Config) -> None:
    """Migration 0008: codes made before were too short to keep."""
    command.downgrade(alembic_config, "0007")
    connection = alembic_config.attributes["connection"]
    admin_id = connection.scalar(
        text("INSERT INTO admin (username) VALUES ('ada') RETURNING id")
    )
    connection.execute(
        text(
            "INSERT INTO recovery_code (admin_id, code_hash) VALUES "
            "(:a, 'unused'), (:a, 'used')"
        ),
        {"a": admin_id},
    )
    connection.execute(
        text("UPDATE recovery_code SET used_at = now() WHERE code_hash = 'used'")
    )
    command.upgrade(alembic_config, "head")
    rows = connection.execute(
        text("SELECT code_hash, voided_at IS NOT NULL FROM recovery_code")
    ).all()
    assert sorted(rows) == [(b"unused", True), (b"used", False)]


def test_extra_current_labels_are_revoked(alembic_config: Config) -> None:
    """Migration 0012: one current label for each thing, the oldest."""
    command.downgrade(alembic_config, "0011")
    connection = alembic_config.attributes["connection"]
    store_id = connection.scalar(
        text("INSERT INTO location (name, kind) VALUES ('Hub', 'store') RETURNING id")
    )
    connection.execute(
        text(
            "INSERT INTO label (code, location_id) VALUES "
            "('2222222222', :s), ('3333333333', :s)"
        ),
        {"s": store_id},
    )
    command.upgrade(alembic_config, "head")
    rows = connection.execute(
        text("SELECT code, revoked_at IS NOT NULL FROM label ORDER BY id")
    ).all()
    assert rows == [("2222222222", False), ("3333333333", True)]


def test_totp_keys_are_sealed_and_come_back(alembic_config: Config) -> None:
    """Migration 0016: each current authenticator's key sealed with
    TOTP_KEYS_DIR's newest version, for its administrator; and opened again
    going down."""
    command.downgrade(alembic_config, "0015")
    connection = alembic_config.attributes["connection"]
    admin_id = connection.scalar(
        text("INSERT INTO admin (username) VALUES ('ada') RETURNING id")
    )
    connection.execute(
        text(
            "INSERT INTO authenticator (admin_id, name, totp_key) "
            "VALUES (:a, 'Phone', 'JBSWY3DPEHPK3PXP')"
        ),
        {"a": admin_id},
    )
    command.upgrade(alembic_config, "head")
    version, sealed = connection.execute(
        text("SELECT key_version, sealed_key FROM authenticator")
    ).one()
    assert b"JBSWY3DPEHPK3PXP" not in sealed
    assert TOTP_KEYS.open(version, sealed, admin_id) == "JBSWY3DPEHPK3PXP"
    command.downgrade(alembic_config, "0015")
    assert connection.scalar(text("SELECT totp_key FROM authenticator")) == (
        "JBSWY3DPEHPK3PXP"
    )


def test_item_names_differing_only_by_punctuation_are_named_first(
    alembic_config: Config,
) -> None:
    """Migration 0023: the index cannot be made over items it would refuse,
    so it names them by id to rename."""
    command.downgrade(alembic_config, "0022")
    connection = alembic_config.attributes["connection"]
    category_id = connection.scalar(
        text("INSERT INTO category (name) VALUES ('Cables') RETURNING id")
    )
    tough, spaced, _ = (
        connection.scalar(
            text("INSERT INTO item (name, category_id) VALUES (:n, :c) RETURNING id"),
            {"n": name, "c": category_id},
        )
        for name in ("ToughCable", "Tough cable", "Patch lead")
    )
    with pytest.raises(RuntimeError, match=f"by id: {tough}, {spaced}\\. Rename"):
        command.upgrade(alembic_config, "head")
    connection.execute(
        text("UPDATE item SET name = 'Tough cable Pro' WHERE id = :i"), {"i": spaced}
    )
    command.upgrade(alembic_config, "head")


def test_package_names_clashing_with_retired_ones_are_named_first(
    alembic_config: Config,
) -> None:
    """Migration 0030: a package's name is its own among its item's,
    retired ones too, compared as items' are, so packages it would refuse
    are named by id to rename; nothing is renamed for them."""
    command.downgrade(alembic_config, "0029")
    connection = alembic_config.attributes["connection"]
    category_id = connection.scalar(
        text("INSERT INTO category (name) VALUES ('Cables') RETURNING id")
    )
    item_id = connection.scalar(
        text("INSERT INTO item (name, category_id) VALUES ('Cat6', :c) RETURNING id"),
        {"c": category_id},
    )
    old, spool, _ = (
        connection.scalar(
            text(
                "INSERT INTO item_package (item_id, name, size, retired_at)"
                " VALUES (:i, :n, 305, :r) RETURNING id"
            ),
            {"i": item_id, "n": name, "r": retired},
        )
        for name, retired in (
            ("Spool", "2026-11-01T00:00:00Z"),
            ("spool!", None),
            ("Box", None),
        )
    )
    with pytest.raises(RuntimeError, match=f"by id: {old}, {spool}\\. Rename"):
        command.upgrade(alembic_config, "head")
    assert (
        connection.scalar(
            text("SELECT name FROM item_package WHERE id = :i"), {"i": spool}
        )
        == "spool!"
    )
    connection.execute(
        text("UPDATE item_package SET name = 'Spool 305 m' WHERE id = :i"),
        {"i": spool},
    )
    # A too-short length of a whole one or more is named too.
    connection.execute(
        text("UPDATE item_package SET short_below = 305 WHERE id = :i"), {"i": spool}
    )
    with pytest.raises(RuntimeError, match=f"by id: {spool}\\. Lower it"):
        command.upgrade(alembic_config, "head")
    connection.execute(
        text("UPDATE item_package SET short_below = 5 WHERE id = :i"), {"i": spool}
    )
    command.upgrade(alembic_config, "head")
    command.downgrade(alembic_config, "0029")


def test_going_back_before_decimal_points_names_what_would_clash(
    alembic_config: Config,
) -> None:
    """Migration 0031: back to a key that drops a decimal point, items and
    packages named so are named by id to rename; nothing is renamed."""
    # Before 0032, whose folded columns these rows do not fill.
    command.downgrade(alembic_config, "0031")
    connection = alembic_config.attributes["connection"]
    category_id = connection.scalar(
        text("INSERT INTO category (name) VALUES ('Cables') RETURNING id")
    )

    def item(name: str) -> int:
        return connection.scalar(
            text("INSERT INTO item (name, category_id) VALUES (:n, :c) RETURNING id"),
            {"n": name, "c": category_id},
        )

    long, short = item("Spool 305 m"), item("Spool 30.5 m")
    reel, half = (
        connection.scalar(
            text(
                "INSERT INTO item_package (item_id, name, size)"
                " VALUES (:i, :n, 1) RETURNING id"
            ),
            {"i": long, "n": name},
        )
        for name in ("Reel 1.5", "Reel 15")
    )
    with pytest.raises(
        RuntimeError,
        match=f"by id: items {long}, {short}; packages {reel}, {half}\\. Rename",
    ):
        command.downgrade(alembic_config, "0030")
    connection.execute(
        text("UPDATE item SET name = 'Spool, short' WHERE id = :i"), {"i": short}
    )
    connection.execute(
        text("UPDATE item_package SET name = 'Reel, half' WHERE id = :i"), {"i": half}
    )
    command.downgrade(alembic_config, "0030")
    command.upgrade(alembic_config, "head")


def test_going_back_before_package_forms_names_what_it_cannot_hold(
    alembic_config: Config,
) -> None:
    """Migration 0024's downgrade: before it, an item was on one line of a
    transaction, so one on two, in different forms, is named by id."""
    # Before 0032, whose folded columns these rows do not fill.
    command.downgrade(alembic_config, "0031")
    connection = alembic_config.attributes["connection"]
    category_id = connection.scalar(
        text("INSERT INTO category (name) VALUES ('Cables') RETURNING id")
    )
    item_id = connection.scalar(
        text("INSERT INTO item (name, category_id) VALUES ('Cat6', :c) RETURNING id"),
        {"c": category_id},
    )
    package_id = connection.scalar(
        text(
            "INSERT INTO item_package (item_id, name, size)"
            " VALUES (:i, 'Spool', 305) RETURNING id"
        ),
        {"i": item_id},
    )
    place = connection.scalar(
        text("INSERT INTO location (name, kind) VALUES ('Store', 'store') RETURNING id")
    )
    receipt = connection.scalar(
        text(
            "INSERT INTO ledger_transaction"
            " (idempotency_key, kind, to_location_id, time_zone)"
            " VALUES (gen_random_uuid(), 'receipt', :p, 'UTC') RETURNING id"
        ),
        {"p": place},
    )
    connection.execute(
        text(
            "INSERT INTO movement (transaction_id, item_id, quantity, to_package_id)"
            " VALUES (:t, :i, 305, :k), (:t, :i, 5, NULL)"
        ),
        {"t": receipt, "i": item_id, "k": package_id},
    )
    with pytest.raises(RuntimeError, match=f"by id: {receipt}\\. 0023"):
        command.downgrade(alembic_config, "0023")


def _insert(connection: Connection, table: str, **values: object) -> int:
    """A row of `table` with `values`; its id."""
    return connection.scalar(
        text(
            f"INSERT INTO {table} ({', '.join(values)})"
            f" VALUES ({', '.join(f':{column}' for column in values)})"
            " RETURNING id"
        ),
        values,
    )


def _item(connection: Connection, category_id: int, name: str) -> int:
    """An item as 0034 holds one, its search text 'kept'."""
    return connection.scalar(
        text(
            "INSERT INTO item (name, category_id, search_text, name_folded,"
            " folded_with) VALUES (:n, :c, 'kept', 'kept', 'x') RETURNING id"
        ),
        {"n": name, "c": category_id},
    )


def test_names_are_rewritten_in_canonical_spelling(alembic_config: Config) -> None:
    """Migration 0035: names in canonical spelling, in place; m² and ﬁ, which
    NFKC would change, and case, kept; what searches compare unchanged."""
    command.downgrade(alembic_config, "0034")
    connection = alembic_config.attributes["connection"]
    category_id = connection.scalar(
        text("INSERT INTO category (name) VALUES ('Sensors 85℃') RETURNING id")
    )

    # The Ohm sign, a decomposed é, the micro sign, ℉.
    ohm, cafe, ligature = (
        _item(connection, category_id, "50Ω load"),
        _item(connection, category_id, "Café µF"),
        _item(connection, category_id, "ﬁber 1 m² 72℉"),
    )
    # Not 0036, which folds what is searched again.
    command.upgrade(alembic_config, "0035")
    names = dict(connection.execute(text("SELECT id, name FROM item")).all())
    assert names == {
        ohm: "50Ω load",
        cafe: "Café μF",
        ligature: "ﬁber 1 m² 72°F",
    }
    assert connection.scalar(
        text("SELECT name FROM category WHERE id = :c"), {"c": category_id}
    ) == ("Sensors 85°C")
    assert set(connection.scalars(text("SELECT search_text FROM item"))) == {"kept"}


def test_names_clashing_once_canonical_are_named_first(alembic_config: Config) -> None:
    """Migration 0035: names that would be one once canonical are named by id
    to rename, whichever table's; nothing is renamed until none would."""
    command.downgrade(alembic_config, "0034")
    connection = alembic_config.attributes["connection"]
    category_id = connection.scalar(
        text("INSERT INTO category (name) VALUES ('Capacitors') RETURNING id")
    )
    micro, greek = (
        _item(connection, category_id, name) for name in ("Cap 10µF", "cap 10 μf")
    )
    store, shelf = (
        connection.scalar(
            text(
                "INSERT INTO location (name, kind, search_text, folded_with)"
                " VALUES (:n, 'store', '', '') RETURNING id"
            ),
            {"n": name},
        )
        for name in ("Cold room 4℃", "cold room 4°c")
    )
    with pytest.raises(
        RuntimeError,
        match=f"by id: item {micro}, {greek}; location {store}, {shelf}\\. Rename",
    ):
        command.upgrade(alembic_config, "head")
    assert connection.scalar(
        text("SELECT name FROM item WHERE id = :i"), {"i": micro}
    ) == ("Cap 10µF")
    connection.execute(
        text("UPDATE item SET name = 'Cap 10 mF' WHERE id = :i"), {"i": greek}
    )
    connection.execute(
        text("UPDATE location SET name = 'Cold room 2' WHERE id = :i"), {"i": shelf}
    )
    command.upgrade(alembic_config, "head")
    assert connection.scalar(
        text("SELECT name FROM item WHERE id = :i"), {"i": micro}
    ) == ("Cap 10μF")


def test_what_is_searched_is_folded_again_with_units(alembic_config: Config) -> None:
    """Migration 0036: rows folded before units were read are folded again,
    as the app folds them, each field alone; rows folded so are left."""
    command.downgrade(alembic_config, "0035")
    connection = alembic_config.attributes["connection"]
    category_id = connection.scalar(
        text("INSERT INTO category (name) VALUES ('Capacitors') RETURNING id")
    )
    stale = _item(connection, category_id, "Cap 10 μF")
    connection.execute(
        text("UPDATE item SET manufacturer = 'M Corp' WHERE id = :i"), {"i": stale}
    )
    current = _item(connection, category_id, "Cap 4.7 kΩ")
    connection.execute(
        text("UPDATE item SET folded_with = :f WHERE id = :i"),
        {"f": FOLDED_WITH, "i": current},
    )
    place = connection.scalar(
        text(
            "INSERT INTO location (name, kind, search_text, folded_with)"
            " VALUES ('Cold room 4 °C', 'store', 'kept', 'x') RETURNING id"
        )
    )
    command.upgrade(alembic_config, "0036")
    rows = connection.execute(
        text("SELECT id, search_text, name_folded, folded_with FROM item")
    ).all()
    assert {row[0]: tuple(row[1:]) for row in rows} == {
        stale: ("Cap 10uF M Corp  ", "Cap 10uF", FOLDED_WITH),
        current: ("kept", "kept", FOLDED_WITH),
    }
    assert connection.execute(
        text("SELECT search_text, folded_with FROM location WHERE id = :i"),
        {"i": place},
    ).one() == ("Cold room 4degC", FOLDED_WITH)


def test_what_is_searched_is_kept_as_typed_too(alembic_config: Config) -> None:
    """Migration 0039: every row folded again, each field as typed and with
    its units respelt (models.held), an item's current aliases too; 'in'
    no unit. Every row: 0036 stamped its rows with this FOLDED_WITH."""
    command.downgrade(alembic_config, "0038")
    connection = alembic_config.attributes["connection"]
    category_id = connection.scalar(
        text("INSERT INTO category (name) VALUES ('Cables') RETURNING id")
    )

    def row(table: str, name: str, searched: bool = True, **values: object) -> int:
        """A row named `name`, its search text 'kept' if it has one, stamped
        as 0036 stamps rows on a database made new."""
        values = {"name": name, "name_key": name_key(name), **values}
        if searched:
            values |= {"search_text": "kept", "folded_with": FOLDED_WITH}
        return _insert(connection, table, **values)

    cable = row("item", "Cable 100 feet", category_id=category_id, name_folded="kept")
    row("item_alias", "Patch 2 in 1", searched=False, item_id=cable)
    row(
        "item_alias",
        "Gone 5 V",
        searched=False,
        item_id=cable,
        retired_at=datetime.now(UTC),
    )
    reel = row("item_package", "Reel 305 m", item_id=cable, size=1)
    command.upgrade(alembic_config, "0039")
    assert connection.execute(
        text("SELECT search_text, name_folded, folded_with FROM item")
    ).one() == ("Cable 100 feet Cable 100ft    Patch 2 in 1", "kept", FOLDED_WITH)
    assert connection.scalar(
        text("SELECT search_text FROM item_package WHERE id = :i"), {"i": reel}
    ) == ("Reel 305 m Reel 305m")


def test_name_keys_are_filled_by_the_migration(alembic_config: Config) -> None:
    """Migration 0037: each item's and package's key, filled by its CHECK's
    own expression."""
    command.downgrade(alembic_config, "0036")
    connection = alembic_config.attributes["connection"]
    category_id = connection.scalar(
        text("INSERT INTO category (name) VALUES ('Cables') RETURNING id")
    )
    spool = _item(connection, category_id, "Spool, 30.5 M")
    connection.execute(
        text(
            "INSERT INTO item_package (item_id, name, size, search_text,"
            " folded_with) VALUES (:i, 'Câble Reel', 1, '', '')"
        ),
        {"i": spool},
    )
    command.upgrade(alembic_config, "head")
    assert connection.execute(
        text(
            "SELECT i.name_key, p.name_key FROM item i"
            " JOIN item_package p ON p.item_id = i.id"
        )
    ).one() == ("spool30.5m", "câblereel")


def test_name_keys_name_what_would_clash_going_up(alembic_config: Config) -> None:
    """Migration 0037's upgrade: names a database's locale kept apart but
    the keys make one, as an ICU locale's index kept a$b and ab, are named
    by id to rename; nothing is changed until none are. An index on the
    name as typed stands for such a locale's."""
    command.downgrade(alembic_config, "0036")
    connection = alembic_config.attributes["connection"]
    connection.execute(text("DROP INDEX uq_item_name"))
    connection.execute(text("CREATE UNIQUE INDEX uq_item_name ON item (name)"))
    category_id = connection.scalar(
        text("INSERT INTO category (name) VALUES ('Cables') RETURNING id")
    )
    signed, plain = (_item(connection, category_id, name) for name in ("a$b", "ab"))
    with pytest.raises(RuntimeError, match=f"by id: item {signed}, {plain}\\. Rename"):
        command.upgrade(alembic_config, "head")
    assert not connection.scalar(
        text(
            "SELECT count(*) FROM information_schema.columns"
            " WHERE column_name = 'name_key'"
        )
    )


def _key_before(name: str) -> str:
    """`name`'s key as 0037 wrote it: no look-alike read as ASCII's."""
    return "".join(c.lower() if c.isascii() else c for c in re.sub(IGNORED, "", name))


def _keyed(connection: Connection, table: str, name: str, **values: object) -> int:
    """A row of `table` named `name`, keyed as 0037 keys it, as 0039
    holds one."""
    values = {"name": name, "name_key": _key_before(name), **values}
    if table != "item_alias":
        values |= {"search_text": "", "folded_with": ""}
    return _insert(connection, table, **values)


def test_name_keys_read_look_alikes_as_ascii(alembic_config: Config) -> None:
    """Migration 0040: each item's, package's and alias's key refilled,
    reading a look-alike space, quote or dash as ASCII's; names kept; and
    back again."""
    command.downgrade(alembic_config, "0039")
    connection = alembic_config.attributes["connection"]
    category_id = connection.scalar(
        text("INSERT INTO category (name) VALUES ('Cables') RETURNING id")
    )
    tough = _keyed(
        connection, "item", "Tough\u00a0cable", category_id=category_id, name_folded=""
    )
    _keyed(connection, "item_package", "Reel\u2013A", item_id=tough, size=1)
    _keyed(connection, "item_alias", "Ubiquiti\u2019s", item_id=tough)
    query = text(
        "SELECT i.name, i.name_key, p.name, p.name_key, a.name, a.name_key"
        " FROM item i JOIN item_package p ON p.item_id = i.id"
        " JOIN item_alias a ON a.item_id = i.id"
    )
    before = connection.execute(query).one()
    command.upgrade(alembic_config, "head")
    assert connection.execute(query).one() == (
        *("Tough\u00a0cable", "toughcable"),
        *("Reel\u2013A", "reela"),
        *("Ubiquiti\u2019s", "ubiquitis"),
    )
    command.downgrade(alembic_config, "0039")
    assert connection.execute(query).one() == before
    command.upgrade(alembic_config, "head")


def test_aliases_names_are_folded(alembic_config: Config) -> None:
    """Migration 0041: each alias's name folded, as the app folds it,
    retired ones too."""
    command.downgrade(alembic_config, "0040")
    connection = alembic_config.attributes["connection"]
    category_id = connection.scalar(
        text("INSERT INTO category (name) VALUES ('Cables') RETURNING id")
    )
    cable = _keyed(connection, "item", "Cable", category_id=category_id, name_folded="")
    for name, retired in (("Câble 10 μF", None), ("Pätch", datetime.now(UTC))):
        _keyed(connection, "item_alias", name, item_id=cable, retired_at=retired)
    command.upgrade(alembic_config, "head")
    assert connection.execute(
        text("SELECT name_folded FROM item_alias ORDER BY id")
    ).scalars().all() == [fold("Câble 10 μF"), "Patch"]


def test_look_alike_name_keys_name_what_would_clash(alembic_config: Config) -> None:
    """Migration 0040's upgrade: names it would make one, among items, an
    item's packages, current aliases, or an alias and an item's name, are
    named by id to rename; nothing is changed until none are. A retired
    alias clashes with nothing."""
    command.downgrade(alembic_config, "0039")
    connection = alembic_config.attributes["connection"]
    category_id = connection.scalar(
        text("INSERT INTO category (name) VALUES ('Cables') RETURNING id")
    )
    item = partial(_keyed, connection, "item", category_id=category_id, name_folded="")
    plain, spaced = item("Tough cable"), item("Tough\u00a0cable")
    patch = item("Patch-cord")
    reel, dashed = (
        _keyed(connection, "item_package", name, item_id=plain, size=1)
        for name in ("Reel A", "Reel\u2013A")
    )
    alias = partial(_keyed, connection, "item_alias", item_id=plain)
    quoted, curly, cord = (
        alias("Ubiquiti's"),
        alias("Ubiquiti\u2019s"),
        alias("Patch\u2014cord"),
    )
    for name in ("Ubiquiti\u02bcs", "Patch\u2212cord"):
        alias(name, retired_at=datetime.now(UTC))
    with pytest.raises(RuntimeError) as refused:
        command.upgrade(alembic_config, "head")
    assert str(refused.value) == (
        "Names differing only by look-alike spaces, quotes or dashes would be"
        f" one name, by id: item {plain}, {spaced}; item_package {reel}, {dashed};"
        f" item_alias {quoted}, {curly}; item_alias {cord} and item {patch}."
        " Rename all but one in each, then migrate again."
    )
    assert (
        connection.scalar(
            text("SELECT name_key FROM item WHERE id = :i"), {"i": spaced}
        )
        == "tough\u00a0cable"
    )


def test_going_back_before_name_keys_names_what_would_clash(
    alembic_config: Config,
) -> None:
    """Migration 0037's downgrade: names the database's locale makes one,
    which the keys keep apart, are named by id to rename; nothing is
    changed until none are."""
    connection = alembic_config.attributes["connection"]
    if connection.scalar(text("SELECT lower('Â')")) == "Â":
        pytest.skip("Under a C locale the keys before were the same.")
    # Keys as 0037 wrote them, which keep an en dash.
    command.downgrade(alembic_config, "0039")
    category_id = connection.scalar(
        text("INSERT INTO category (name) VALUES ('Cables') RETURNING id")
    )

    def row(table: str, name: str, key: str, **values: object) -> int:
        return _insert(
            connection,
            table,
            name=name,
            name_key=key,
            search_text="",
            folded_with="",
            **values,
        )

    lower, upper = (
        row("item", name, key, category_id=category_id, name_folded="")
        for name, key in (("Câble", "câble"), ("CÂBLE", "cÂble"))
    )
    reel, dashed = (
        row("item_package", name, key, item_id=lower, size=1)
        for name, key in (("Reel 1", "reel1"), ("Reel\u20131", "reel\u20131"))
    )
    with pytest.raises(
        RuntimeError,
        match=f"by id: item {lower}, {upper}; item_package {reel}, {dashed}\\. Rename",
    ):
        command.downgrade(alembic_config, "0036")
    connection.execute(
        text("UPDATE item SET name = 'Câble 2', name_key = 'câble2' WHERE id = :i"),
        {"i": upper},
    )
    connection.execute(
        text(
            "UPDATE item_package SET name = 'Reel 2', name_key = 'reel2' WHERE id = :i"
        ),
        {"i": dashed},
    )
    command.downgrade(alembic_config, "0036")
    command.upgrade(alembic_config, "head")


def test_what_is_searched_reads_units_as_the_forms_do(alembic_config: Config) -> None:
    """Migration 0043: every row folded again as search now reads units, as
    the forms do (option D): 10G as Gbps, Cat6a no unit, an item's and a
    package's G by the reading rules there are, a place's not; items' and
    aliases' name_folded too."""
    command.downgrade(alembic_config, "0042")
    connection = alembic_config.attributes["connection"]
    category_id = connection.scalar(
        text("INSERT INTO category (name) VALUES ('Links') RETURNING id")
    )

    def row(table: str, name: str, **values: object) -> int:
        values = {"name": name, "name_key": name_key(name), **values}
        if table != "item_alias":
            values |= {"search_text": "kept", "folded_with": "kept"}
        return _insert(connection, table, **values)

    link = row(
        "item", "Box 10G", category_id=category_id, name_folded="kept", unit_key=""
    )
    cat = row("item_alias", "Cat6a 4 K", item_id=link, name_folded="kept", unit_key="")
    gone = row(
        "item_alias",
        "Gone 2 K",
        item_id=link,
        name_folded="kept",
        unit_key="",
        retired_at=datetime.now(UTC),
    )
    reel = row("item_package", "Tape 3M", item_id=link, size=1, unit_key="")
    place = _insert(
        connection,
        "location",
        name="Rack 10G",
        kind="store",
        search_text="kept",
        folded_with="kept",
    )
    _insert(connection, "reading_rule", token="M", reading=None)
    _insert(connection, "reading_rule", token="G", reading="g")
    command.upgrade(alembic_config, "0043")
    assert connection.execute(
        text("SELECT search_text, name_folded, folded_with FROM item")
    ).one() == (
        # Its G grams by the rule, so as typed; its name_folded by none.
        "Box 10G    Cat6a 4 K Cat6a 4kbit/s",
        "Box 10Gbit/s",
        FOLDED_WITH,
    )
    # A retired alias folded too, but not searched.
    assert connection.execute(
        text("SELECT id, name_folded FROM item_alias ORDER BY id")
    ).all() == [(cat, "Cat6a 4kbit/s"), (gone, "Gone 2kbit/s")]
    assert (
        connection.scalar(
            text("SELECT search_text FROM item_package WHERE id = :i"), {"i": reel}
        )
        == "Tape 3M"
    )
    assert (
        connection.scalar(
            text("SELECT search_text FROM location WHERE id = :i"), {"i": place}
        )
        == "Rack 10G Rack 10Gbit/s"
    )


def test_unit_keys_are_read_again_as_the_forms_read(alembic_config: Config) -> None:
    """Migration 0043: every unit key read again, by the rules there are;
    names that would then be one named by id first, nothing changed."""
    command.downgrade(alembic_config, "0042")
    connection = alembic_config.attributes["connection"]
    category_id = connection.scalar(
        text("INSERT INTO category (name) VALUES ('Links') RETURNING id")
    )

    def item(name: str, key: str) -> int:
        return _insert(
            connection,
            "item",
            name=name,
            name_key=name_key(name),
            unit_key=key,
            category_id=category_id,
            name_folded="",
            search_text="",
            folded_with="",
        )

    box = item("Box 10G", "stale")
    package = _insert(
        connection,
        "item_package",
        name="Tape 3M",
        name_key=name_key("Tape 3M"),
        unit_key="stale",
        item_id=box,
        size=1,
        search_text="",
        folded_with="",
    )
    _insert(connection, "reading_rule", token="M", reading="m")

    def alias(name: str, **values: object) -> int:
        return _insert(
            connection,
            "item_alias",
            name=name,
            name_key=name_key(name),
            unit_key=f"stale {name}",
            item_id=box,
            name_folded="",
            **values,
        )

    # Retired, it clashes with nothing.
    alias("Box 10 Gbit/s", retired_at=datetime.now(UTC))
    command.upgrade(alembic_config, "0043")
    assert connection.execute(
        text("SELECT unit_key FROM item UNION ALL SELECT unit_key FROM item_package")
    ).scalars().all() == ["box10gbits", "tape3m"]
    assert package
    command.downgrade(alembic_config, "0042")
    other = item("Box 10 Gbps", "other")
    with pytest.raises(RuntimeError) as refused:
        command.upgrade(alembic_config, "0043")
    assert f"by id: item {box}, {other}." in str(refused.value)
    connection.execute(text("DELETE FROM item WHERE id = :i"), {"i": other})
    current = alias("Box 10 Gbit/s")
    with pytest.raises(RuntimeError) as refused:
        command.upgrade(alembic_config, "0043")
    assert f"by id: item_alias {current} and item {box}." in str(refused.value)


def test_unit_keys_are_filled_by_the_migration(alembic_config: Config) -> None:
    """Migration 0042: each item's, package's and alias's unit key, as the
    app reads names with no rule settled yet; and gone again, going back."""
    command.downgrade(alembic_config, "0041")
    connection = alembic_config.attributes["connection"]
    category_id = connection.scalar(
        text("INSERT INTO category (name) VALUES ('Parts') RETURNING id")
    )
    cap = _keyed(
        connection, "item", "Cap 10 uF", category_id=category_id, name_folded=""
    )
    _keyed(connection, "item_package", "Reel 305 M", item_id=cap, size=305)
    _keyed(connection, "item_alias", "Link 1 Gbps", item_id=cap, name_folded="")
    command.upgrade(alembic_config, "head")
    assert connection.execute(
        text(
            "SELECT unit_key FROM item UNION ALL SELECT unit_key FROM item_package"
            " UNION ALL SELECT unit_key FROM item_alias"
        )
    ).scalars().all() == ["cap10uf", "reel305m", "link1gbits"]
    assert unit_key("Reel 305 M") == "reel305m"
    command.downgrade(alembic_config, "0041")
    assert not connection.scalar(
        text(
            "SELECT count(*) FROM information_schema.columns"
            " WHERE column_name = 'unit_key'"
        )
    )


def test_unit_keys_name_what_would_clash(alembic_config: Config) -> None:
    """Migration 0042's upgrade: names that would read alike once units are
    read, among items, an item's packages, current aliases, or an alias and
    an item's name, are named by id to rename. A retired alias clashes with
    nothing."""
    command.downgrade(alembic_config, "0041")
    connection = alembic_config.attributes["connection"]
    category_id = connection.scalar(
        text("INSERT INTO category (name) VALUES ('Parts') RETURNING id")
    )
    item = partial(_keyed, connection, "item", category_id=category_id, name_folded="")
    cap, micro = item("Cap 10 uF"), item("Cap 10μF")
    link = item("Link 1Gbit/s")
    bag, bagged = (
        _keyed(connection, "item_package", name, item_id=cap, size=1)
        for name in ("Bag 2 uF", "Bag 2μF")
    )
    alias = partial(_keyed, connection, "item_alias", item_id=cap, name_folded="")
    probe, degrees, gbps = (
        alias("Probe 85 Cel"),
        alias("Probe 85°C"),
        alias("Link 1 Gbps"),
    )
    alias("Probe 85 degC", retired_at=datetime.now(UTC))
    with pytest.raises(RuntimeError) as refused:
        command.upgrade(alembic_config, "head")
    assert str(refused.value) == (
        "Names differing only in how their units are spelt would be one name, by"
        f" id: item {cap}, {micro}; item_package {bag}, {bagged}; item_alias"
        f" {probe}, {degrees}; item_alias {gbps} and item {link}. Rename all but"
        " one in each, then migrate again."
    )
