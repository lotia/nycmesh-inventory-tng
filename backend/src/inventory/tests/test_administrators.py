"""The system always has an administrator, and can be given its first one.

`ensure_administrator` carries the argument for the command and migration 0020
for the trigger. `inventory-tng-s8dk` is the issue, and the project owner's
requirement is the thing to keep in view: the account made at deploy time is an
ORDINARY administrator, deletable like any other, and the system must never be
left with none.

Those two pull against each other, which is why both halves are held here.
"""

from collections.abc import Callable

import pytest
from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, connection, transaction

pytestmark = pytest.mark.django_db

PASSWORD = "DJANGO_SUPERUSER_PASSWORD"


@pytest.fixture
def administrator() -> User:
    """One administrator, so a test can remove things without hitting the floor."""
    return User.objects.create_user("keeper", password="not-a-real-password", is_staff=True, is_active=True)


# ---------------------------------------------------------------------------
# The floor: at least one active administrator
# ---------------------------------------------------------------------------


def refused(action: Callable[[], object]) -> bool:
    """Whether the database refused, without leaving the test in a broken transaction.

    `SET CONSTRAINTS ALL IMMEDIATE` is what makes this work at all, and the
    reason is worth having written down. The trigger is DEFERRED, so it fires
    when a transaction commits -- and a test never commits: pytest-django wraps
    each one in a transaction it rolls back, so the inner `atomic` is a
    savepoint and deferred constraints are not checked at a savepoint. Without
    this line every assertion below passes for the wrong reason, having tested
    a guard that never ran.

    The `atomic` block is not decoration either: a statement the database
    rejects leaves the connection unusable until the surrounding block ends.
    """
    try:
        with transaction.atomic():
            action()
            with connection.cursor() as cursor:
                cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
    except IntegrityError:
        return True
    return False


def test_the_last_administrator_cannot_be_deleted(administrator: User) -> None:
    assert refused(administrator.delete), (
        "the last administrator was deleted, leaving nobody who can reach the admin and no way "
        "back through the application"
    )


def test_the_last_administrator_cannot_be_demoted(administrator: User) -> None:
    """The likelier accident, and the reason a DELETE guard alone is not enough."""
    assert refused(lambda: User.objects.filter(pk=administrator.pk).update(is_staff=False))


def test_the_last_administrator_cannot_be_deactivated(administrator: User) -> None:
    """The third door. An inactive administrator cannot sign in, so they are not one."""
    assert refused(lambda: User.objects.filter(pk=administrator.pk).update(is_active=False))


def test_a_handover_is_not_blocked(administrator: User) -> None:
    """Add the incoming one first and every step satisfies the rule.

    This is the case that would make the guard intolerable if it failed, and
    the reason the rule is "at least one remains" rather than "this row is
    protected".
    """
    incoming = User.objects.create_user("incoming", password="x", is_staff=True, is_active=True)

    administrator.delete()

    assert User.objects.filter(is_staff=True, is_active=True).get() == incoming


def test_an_ordinary_user_is_not_protected(administrator: User) -> None:
    """The guard is about administrators, and must not become a guard on deletion."""
    ordinary = User.objects.create_user("volunteer-account", password="x")

    ordinary.delete()

    assert not User.objects.filter(username="volunteer-account").exists()


def test_an_edit_that_touches_neither_flag_is_left_alone(administrator: User) -> None:
    """By far the commonest write to this row: a password, a name, a login time."""
    User.objects.filter(pk=administrator.pk).update(first_name="Sam")

    administrator.refresh_from_db()
    assert administrator.first_name == "Sam"


def test_promoting_somebody_is_never_refused(administrator: User) -> None:
    ordinary = User.objects.create_user("rising", password="x")

    User.objects.filter(pk=ordinary.pk).update(is_staff=True)

    assert User.objects.filter(is_staff=True, is_active=True).count() == 2


def test_it_holds_against_a_writer_that_is_not_django(administrator: User) -> None:
    """Decision 0016's whole point, and the reason this is a trigger.

    Raw SQL is what the admin, a fixture, a shell and any future client all
    reduce to. A rule enforced in a serializer would not be here at all.
    """

    def straight_at_the_table() -> None:
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM auth_user WHERE id = %s", [administrator.pk])

    assert refused(straight_at_the_table)


# ---------------------------------------------------------------------------
# Giving the system its first one
# ---------------------------------------------------------------------------


def test_it_makes_the_first_administrator(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(PASSWORD, "not-a-real-password")

    call_command("ensure_administrator", "--username=first")

    made = User.objects.get(username="first")
    assert made.is_staff and made.is_superuser, "the account cannot reach the admin, so it is not one"


def test_it_is_an_ordinary_administrator(monkeypatch: pytest.MonkeyPatch) -> None:
    """The owner's requirement: nothing marks it, and it goes like any other.

    Removable once a second exists, which is the same rule every other
    administrator is held to rather than an exemption for this one.
    """
    monkeypatch.setenv(PASSWORD, "not-a-real-password")
    call_command("ensure_administrator", "--username=first")
    User.objects.create_user("second", password="x", is_staff=True, is_active=True)

    User.objects.get(username="first").delete()

    assert not User.objects.filter(username="first").exists()


def test_running_it_again_is_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """A re-applied Job or a restarted init container must not fail the deploy."""
    monkeypatch.setenv(PASSWORD, "not-a-real-password")
    call_command("ensure_administrator", "--username=first")

    call_command("ensure_administrator", "--username=first")

    assert User.objects.filter(username="first").count() == 1


def test_running_it_again_changes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """The conservative half of idempotence, and the half worth a test.

    The command's docstring argues why finding an account is not an occasion
    to make it match; this is the pair of ways that would hurt.
    """
    monkeypatch.setenv(PASSWORD, "first-password")
    call_command("ensure_administrator", "--username=first")
    User.objects.create_user("second", password="x", is_staff=True, is_active=True)
    User.objects.filter(username="first").update(is_staff=False)

    monkeypatch.setenv(PASSWORD, "a-different-password")
    call_command("ensure_administrator", "--username=first")

    unchanged = User.objects.get(username="first")
    assert not unchanged.is_staff, "a re-run re-promoted an administrator somebody had demoted"
    assert unchanged.check_password("first-password"), "a re-run reset a password that had been changed"


def test_it_refuses_without_a_username() -> None:
    with pytest.raises(CommandError, match="No username"):
        call_command("ensure_administrator")


def test_it_refuses_without_a_password(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only when it would have to create one -- an existing account needs no password."""
    monkeypatch.delenv(PASSWORD, raising=False)

    with pytest.raises(CommandError, match="No password"):
        call_command("ensure_administrator", "--username=first")


def test_an_existing_account_needs_no_password(monkeypatch: pytest.MonkeyPatch) -> None:
    """So a re-run works in a pod whose secret is no longer mounted."""
    User.objects.create_user("first", password="x", is_staff=True, is_superuser=True)
    monkeypatch.delenv(PASSWORD, raising=False)

    call_command("ensure_administrator", "--username=first")

    assert User.objects.filter(username="first").count() == 1
