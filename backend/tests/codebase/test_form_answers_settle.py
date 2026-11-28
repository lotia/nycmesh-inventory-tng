"""Every administrator page answering a form has its taps wait a moment as
it loads, so the second tap of a double tap lands on nothing (Ali,
2026-11-27, inventory-tng-0c2z; docs/decisions/0019, Double taps): one
sent back by the form's POST is marked by pages.page(), and the redirect
after one is told by the browser (frontend/src/admin/settle-on-load.ts).
So no module answers with a page or a redirect of its own, which would
skip page() and redirect(), and every page's template extends
admin_base.html, which carries the mark."""

import re

from helpers.source import SOURCE, TEMPLATES

# A response made by hand, page or redirect.
MADE = re.compile(r"\b(?:TemplateResponse|HTMLResponse|RedirectResponse)\(")
# Where one is made: page() and redirect(); the scan page's shell and the
# site root's redirect to it, which load no administrator script; and the
# API's docs page, in development only.
MAKERS = {"web/pages.py", "web/frontend_bundle.py", "web/security_headers.py"}
# The templates that are not administrator pages: the base itself, and the
# scan page's shell.
NOT_ADMIN = {"admin_base.html", "frontend_page.html"}


def test_pages_and_redirects_are_made_only_by_page_and_redirect() -> None:
    made = [
        path.relative_to(SOURCE).as_posix()
        for path in sorted(SOURCE.rglob("*.py"))
        if MADE.search(path.read_text())
    ]
    assert [path for path in made if path not in MAKERS] == []


def test_every_page_extends_the_administrator_base() -> None:
    pages = [
        path
        for path in sorted(TEMPLATES.rglob("*.html"))
        if not path.name.startswith("_")
        and path.relative_to(TEMPLATES).as_posix() not in NOT_ADMIN
    ]
    assert pages != []
    assert [
        path.relative_to(TEMPLATES).as_posix()
        for path in pages
        if '{% extends "admin_base.html" %}' not in path.read_text()
    ] == []
