import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text

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
