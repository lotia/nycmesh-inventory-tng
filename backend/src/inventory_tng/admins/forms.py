"""How the administrators pages' forms are read and checked: an
invitation's username and email, and what is wrong, in words, beside the
field it is about or on the administrator's own page. The handlers in
admin/admins.py read the request and pass the form's fields."""

from inventory_tng.accounts import email_problem, username_problem
from inventory_tng.core.phrases import Words, lazy_gettext

TAKEN = lazy_gettext("Another administrator already has that username or email.")
OWN_RESET = lazy_gettext("Change your own on your account page.")
OWN_RETIRE = lazy_gettext("Another administrator must retire you.")


def invite_problems(username: str, email: str) -> dict[str, Words]:
    """What is wrong with an invitation's username and email, stripped."""
    problems = {}
    if problem := username_problem(username):
        problems["username"] = problem
    if problem := email_problem(email):
        problems["email"] = problem
    return problems
