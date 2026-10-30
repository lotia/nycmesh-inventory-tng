from alembic import command
from alembic.config import Config


def test_migrations_match_the_models(alembic_config: Config) -> None:
    command.check(alembic_config)


def test_migrations_go_down_and_back_up(alembic_config: Config) -> None:
    command.downgrade(alembic_config, "base")
    command.upgrade(alembic_config, "head")
