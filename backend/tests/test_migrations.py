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
