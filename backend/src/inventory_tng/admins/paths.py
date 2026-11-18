"""The administrators' pages' paths, which other features' pages and the
templates link to (admins/guards.py)."""

from urllib.parse import urlencode

from inventory_tng.web.paths import ADMIN

HOME = "/admin"
SETUP = "/setup"
SIGN_IN = "/admin/sign-in"
SIGN_OUT = "/admin/sign-out"
CONFIRM = "/admin/confirm"
NEW_AUTHENTICATOR = "/admin/authenticators/new"
ACCOUNT = "/admin/account"
FORGOT = "/admin/forgot"
RESET = "/admin/reset"
ADMINS = f"{ADMIN}/admins"
RECOVER = "/recover"


def reset_url(site: str, token: str) -> str:
    """A reset link, under `site`, such as SITE_URL."""
    return f"{site.rstrip('/')}{RESET}?{urlencode({'token': token})}"
