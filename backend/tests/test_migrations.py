from alembic import command
from alembic.config import Config
from sqlalchemy import text


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
