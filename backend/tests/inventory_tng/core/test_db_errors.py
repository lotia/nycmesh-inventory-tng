"""What attempt() says a refused change broke, and how it logs it."""

from sqlalchemy.orm import Session
from structlog.testing import capture_logs

from inventory_tng.core.db_errors import (
    Broken,
    Stale,
    attempt,
    refusal,
    refusal_naming_index,
)
from inventory_tng.models.catalogue import Category, Item

from helpers.rows import add


def test_a_unique_refusal_names_its_index(session: Session, radios: Category) -> None:
    """Only "unique" to attempt() and refusal(), refusal_naming_index()
    names the index the database said was broken."""
    add(session, Item(name="LiteBeam", category_id=radios.id, old_code="NYCM-1"))

    def clash() -> Item:
        return add(session, Item(name="Omni", category_id=radios.id, old_code="NYCM-1"))

    assert attempt(session, clash) == (None, "unique")
    assert refusal(session, clash, "unique") == (None, "unique")
    assert refusal_naming_index(session, clash, "unique") == (
        None,
        Broken("unique", "uq_item_old_code"),
    )


def test_a_rule_refusal_names_no_index(session: Session, radios: Category) -> None:
    def below_none() -> Item:
        return add(session, Item(name="Omni", category_id=radios.id, minimum=-1))

    assert refusal_naming_index(session, below_none, "unique", "ck_item_minimum") == (
        None,
        Broken("ck_item_minimum"),
    )


def test_a_refusal_is_logged_once_with_its_rule_index_and_ids(
    session: Session, radios: Category
) -> None:
    add(session, Item(name="LiteBeam", category_id=radios.id, old_code="NYCM-2"))

    def clash() -> Item:
        return add(session, Item(name="Omni", category_id=radios.id, old_code="NYCM-2"))

    with capture_logs() as logs:
        attempt(session, clash, category_id=radios.id)
    assert logs == [
        {
            "event": "change refused by a rule",
            "rule": "unique",
            "index": "uq_item_old_code",
            "category_id": radios.id,
            "log_level": "info",
        }
    ]


def test_a_stale_form_is_left_to_its_own_log_line(session: Session) -> None:
    """Logged and counted by db_locking.unchanged, by table."""

    def stale() -> None:
        raise Stale

    with capture_logs() as logs:
        assert attempt(session, stale) == (None, "changed_since_shown")
    assert logs == []
