"""What attempt() says a refused change broke."""

from sqlalchemy.orm import Session

from inventory_tng.core.db_errors import (
    Broken,
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
