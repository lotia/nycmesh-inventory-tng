"""A long list of records (docs/decisions/0019): search that forgives
typos, with suggestions as one types; "Current · Retired · All" (or
another word than Retired, such as labels' Inactive); and pages of
PER_PAGE. Each list page says how to search its kind (Searched) and reads
its query string with Searched.read; _listing.html draws the Listing.

The query string carries it all, so every view is a link: `q`, the search;
`show`, which records; `page`; and `pick`, the one record a suggestion
picked, which narrows the list to it."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from math import ceil
from typing import Any, Literal
from urllib.parse import urlencode

import structlog
from sqlalchemy import (
    ColumnElement,
    Select,
    SQLColumnExpression,
    case,
    func,
    not_,
    or_,
    select,
)
from sqlalchemy.orm import Session

from inventory_tng.models import INTEGER_MAX, fuzzy, holds, whole

log = structlog.get_logger(__name__)

type Show = Literal["current", "retired", "inactive", "all"]
# What the filter between Current and All shows: the records not current.
type Lapsed = Literal["retired", "inactive"]
PER_PAGE = 50
# How many suggestions the search box offers.
SUGGESTED = 8
# The longest search read: enough for any name, and no more work than that.
SEARCH_MAX = 200


def closeness(column: SQLColumnExpression[str], search: str) -> ColumnElement[float]:
    """How close a match is, best highest: holding it as typed first."""
    return case((holds(column, search), 2.0), else_=0.0) + (
        func.greatest(
            func.similarity(column, search), func.word_similarity(search, column)
        )
    )


@dataclass(frozen=True)
class Searched[T]:
    """How a list page, at `here`, finds its kind of record: the model, the
    text a search reads (such as an item's search_text) and its name, by
    which the list is sorted. Optionally: the select of the model it reads,
    if joined to more; a column a search matches only whole, held in
    capitals, such as a label's code, put first; which are current, if not
    those with no retired_at; what the filter between Current and All calls
    the rest; and each row as the page shows it, with a `name`, if not the
    row itself."""

    here: str
    model: type[T]
    text: SQLColumnExpression[str]
    name: SQLColumnExpression[str]
    query: Select | None = None
    whole: SQLColumnExpression[str] | None = None
    current: ColumnElement[bool] | None = None
    lapsed: Lapsed = "retired"
    shown: Callable[[Session, Sequence[T]], Sequence[Any]] | None = None

    def read(
        self, q: str = "", show: str = "", page: str = "", pick: str = ""
    ) -> Listing[T]:
        """The view of the list a query string asks for: anything odd is
        the plain list's, never an error, as a link may have been typed by
        hand."""
        shows: dict[str, Show] = {
            "current": "current",
            self.lapsed: self.lapsed,
            "all": "all",
        }
        return Listing(
            self,
            search=" ".join(q.split())[:SEARCH_MAX],
            show=shows.get(show, "current"),
            page=whole(page, 1, INTEGER_MAX) or 1,
            pick=whole(pick, 1, INTEGER_MAX),
        )

    def base(self, show: Show) -> Select:
        """The records `show` asks for."""
        query = select(self.model) if self.query is None else self.query
        if show == "all":
            return query
        current = self.is_current()
        return query.where(current if show == "current" else not_(current))

    def matching(self, search: str) -> ColumnElement[bool]:
        found = fuzzy(self.text, search)
        if self.whole is None:
            return found
        return or_(found, self.whole == search.upper())

    def closeness(self, search: str) -> ColumnElement[float]:
        """As closeness(), and the whole column matched before anything."""
        near = closeness(self.text, search)
        if self.whole is None:
            return near
        return case((self.whole == search.upper(), 3.0), else_=0.0) + near

    def is_current(self) -> ColumnElement[bool]:
        table: Any = self.model
        return table.retired_at.is_(None) if self.current is None else self.current

    def as_shown(self, session: Session, rows: Sequence[T]) -> Sequence[Any]:
        return rows if self.shown is None else self.shown(session, rows)


@dataclass(frozen=True)
class Listing[T]:
    """One view of a list, as its query string asks for it
    (Searched.read), and what it finds there."""

    searched: Searched[T]
    search: str = ""
    show: Show = "current"
    page: int = 1
    pick: int | None = None
    rows: Sequence[T] = ()
    # The rows as the page shows them (Searched.shown).
    shown: Sequence[Any] = ()
    # How many the view finds, and how many the filter has, unsearched.
    found: int = 0
    total: int = 0
    # The picked record's name, when one was picked.
    picked: str | None = None

    @property
    def here(self) -> str:
        return self.searched.here

    @property
    def lapsed(self) -> Lapsed:
        return self.searched.lapsed

    @property
    def pages(self) -> int:
        return max(1, ceil(self.found / PER_PAGE))

    @property
    def narrowed(self) -> bool:
        """Whether a search or a pick narrows the list."""
        return bool(self.search) or self.pick is not None

    def link(
        self,
        *,
        show: Show | None = None,
        page: int | None = None,
        search: str | None = None,
        pick: int | None = None,
    ) -> str:
        """This view's address, changed: a new filter, search or pick starts
        at page 1, and a pick stays only while just the page changes."""
        view = self
        if show is not None or search is not None or pick is not None:
            view = replace(view, page=1, pick=pick)
        view = replace(
            view,
            show=show or view.show,
            search=view.search if search is None else search,
            page=page or view.page,
        )
        query = {
            "q": view.search,
            "show": "" if view.show == "current" else view.show,
            "page": "" if view.page == 1 else str(view.page),
            "pick": "" if view.pick is None else str(view.pick),
        }
        kept = {key: value for key, value in query.items() if value}
        return self.here + (f"?{urlencode(kept)}" if kept else "")

    def find(self, session: Session) -> Listing[T]:
        """This view's rows: filtered, searched or picked, best match first,
        then by name, one page of them."""
        searched = self.searched
        table: Any = searched.model
        query = searched.base(self.show)
        total = _count(session, query)
        if self.pick is not None:
            # One record at most: no count, nor a page, to work out.
            rows = session.scalars(query.where(table.id == self.pick)).all()
            shown = searched.as_shown(session, rows)
            return replace(
                self,
                rows=rows,
                shown=shown,
                found=len(rows),
                total=total,
                page=1,
                picked=shown[0].name if shown else None,
            )
        if self.search:
            query = query.where(searched.matching(self.search)).order_by(
                searched.closeness(self.search).desc()
            )
        found = _count(session, query) if self.search else total
        page = min(self.page, max(1, ceil(found / PER_PAGE)))
        rows = session.scalars(
            query.order_by(func.lower(searched.name), table.id)
            .limit(PER_PAGE)
            .offset((page - 1) * PER_PAGE)
        ).all()
        log.debug("listed", found=found, total=total, page=page)
        shown = searched.as_shown(session, rows)
        return replace(
            self, rows=rows, shown=shown, found=found, total=total, page=page
        )

    def suggest(
        self, session: Session, hint: Callable[[T], str] = lambda _row: ""
    ) -> list[dict[str, str]]:
        """What the search box offers for what is typed so far: the closest
        records the filter shows, each with its name, a hint to tell it
        apart, and the link that narrows the list to it."""
        if not self.search:
            return []
        searched = self.searched
        rows = session.scalars(
            searched.base(self.show)
            .where(searched.matching(self.search))
            .order_by(
                searched.closeness(self.search).desc(),
                func.lower(searched.name),
            )
            .limit(SUGGESTED)
        ).all()
        log.debug("suggested", found=len(rows))
        return [
            {
                "name": shown.name,
                "hint": hint(row),
                "href": self.link(pick=getattr(row, "id"), search=""),  # noqa: B009
            }
            for row, shown in zip(rows, searched.as_shown(session, rows), strict=True)
        ]


def _count(session: Session, query: Select) -> int:
    return session.scalar(select(func.count()).select_from(query.subquery())) or 0
