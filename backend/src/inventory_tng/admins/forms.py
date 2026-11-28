"""How the administrators pages' forms are read and checked: an
invitation's username and email, setting up the first administrator, and
a new authenticator, and what is wrong, in words, beside the field it is
about or on the administrator's own page. The handlers in admins_routes.py
and sign_in_routes.py read the request and pass the form's fields."""

from inventory_tng.admins import accounts, authenticator_totp
from inventory_tng.core.phrases import Words, gettext, lazy_gettext
from inventory_tng.web.form_problems import Problem

TAKEN = lazy_gettext("Another administrator already has that username or email.")
OWN_RESET = lazy_gettext("Change your own on your account page.")
OWN_RETIRE = lazy_gettext("Another administrator must retire you.")


def invite_problems(username: str, email: str) -> dict[str, Words]:
    """What is wrong with an invitation's username and email, stripped."""
    problems = {}
    if problem := accounts.username_problem(username):
        problems["username"] = problem
    if problem := accounts.email_problem(email):
        problems["email"] = problem
    return problems


NOT_THE_TOKEN = lazy_gettext("That isn't the setup token.")


def code_mismatch() -> str:
    """A new authenticator's code refused, naming the app as it shows us."""
    return gettext(
        "That code didn't match. Check the app shows %(issuer)s, and type the "
        "code it shows now.",
        issuer=authenticator_totp.ISSUER,
    )


def setup_problems(
    username: str, email: str, password: str, password_again: str
) -> dict[str, Words]:
    """What is wrong with the first administrator's username, email and
    password, as typed twice (accounts.password_problems)."""
    problems = {
        field: problem
        for field, problem in (
            ("username", accounts.username_problem(username)),
            ("email", accounts.email_problem(email)),
        )
        if problem
    }
    return problems | accounts.password_problems(password, password_again)


def authenticator_problems(name: str, step: int | None) -> dict[str, Words]:
    """A new authenticator's name, and the step its code was for: None if
    the code did not match."""
    problems = {}
    if problem := accounts.authenticator_name_problem(name):
        problems["authenticator_name"] = problem
    if step is None:
        problems["code"] = Problem(code_mismatch(), "mismatch")
    return problems
