"""The administrators' pages' paths, which other features' pages and the
templates link to (admins/guards.py)."""

from urllib.parse import urlencode

from inventory_tng.web.paths import ADMIN

HOME = ADMIN
SETUP = "/setup"
SIGN_IN = f"{ADMIN}/sign-in"
SIGN_OUT = f"{ADMIN}/sign-out"
CONFIRM = f"{ADMIN}/confirm"
NEW_AUTHENTICATOR = f"{ADMIN}/authenticators/new"
ACCOUNT = f"{ADMIN}/account"
FORGOT = f"{ADMIN}/forgot"
RESET = f"{ADMIN}/reset"
ADMINS = f"{ADMIN}/admins"
RECOVER = "/recover"


def reset_url(site: str, token: str) -> str:
    """A reset link, under `site`, such as SITE_URL."""
    return f"{site.rstrip('/')}{RESET}?{urlencode({'token': token})}"
