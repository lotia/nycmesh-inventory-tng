"""A long list of records (docs/decisions/0019): search that forgives
typos, with suggestions as one types; "Current · Retired · All" (or
another word than Retired, such as labels' Inactive); and pages of
PER_PAGE. Each list page says how to search its kind (Searched) and
reads the query with Listing.read; _listing.html draws it.

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
    """How a list page finds its kind of record: the model, the text a
    search reads (such as an item's search_text) and its name, by which the
    list is sorted. Which are current, if not those with no retired_at; and
    each row's name in words, which a suggestion and a pick show, if not its
    name column's."""

    model: type[T]
    text: SQLColumnExpression[str]
    name: SQLColumnExpression[str]
    current: ColumnElement[bool] | None = None
    words: Callable[[Session, Sequence[T]], Sequence[str]] | None = None

    def is_current(self) -> ColumnElement[bool]:
        table: Any = self.model
        return table.retired_at.is_(None) if self.current is None else self.current

    def names(self, session: Session, rows: Sequence[T]) -> Sequence[str]:
        if self.words is not None:
            return self.words(session, rows)
        return [getattr(row, "name") for row in rows]  # noqa: B009


@dataclass(frozen=True)
class Listing[T]:
    """One view of a list, as its query string asks for it, and what it
    finds there."""

    here: str
    lapsed: Lapsed = "retired"
    search: str = ""
    show: Show = "current"
    page: int = 1
    pick: int | None = None
    rows: Sequence[T] = ()
    # How many the view finds, and how many the filter has, unsearched.
    found: int = 0
    total: int = 0
    # The picked record's name, when one was picked.
    picked: str | None = None

    @classmethod
    def read(
        cls,
        here: str,
        q: str = "",
        show: str = "",
        page: str = "",
        pick: str = "",
        *,
        lapsed: Lapsed = "retired",
    ) -> Listing[T]:
        """As a list page's query string asks: anything odd is the plain
        list's, never an error, as a link may have been typed by hand."""
        shows: dict[str, Show] = {"current": "current", lapsed: lapsed, "all": "all"}
        return cls(
            here=here,
            lapsed=lapsed,
            search=" ".join(q.split())[:SEARCH_MAX],
            show=shows.get(show, "current"),
            page=whole(page, 1, INTEGER_MAX) or 1,
            pick=whole(pick, 1, INTEGER_MAX),
        )

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

    def _shown(self, searched: Searched[T], query: Select) -> Select:
        """`query` narrowed to the records `show` asks for."""
        if self.show == "all":
            return query
        current = searched.is_current()
        return query.where(current if self.show == "current" else not_(current))

    def find(
        self, session: Session, searched: Searched[T], query: Select
    ) -> Listing[T]:
        """This view's rows, of `query` (a select of searched.model, perhaps
        joined): filtered, searched or picked, best match first, then by
        name, one page of them."""
        table: Any = searched.model
        query = self._shown(searched, query)
        total = _count(session, query)
        if self.pick is not None:
            # One record at most: no count, nor a page, to work out.
            rows = session.scalars(query.where(table.id == self.pick)).all()
            picked = searched.names(session, rows)[0] if rows else None
            return replace(
                self, rows=rows, found=len(rows), total=total, page=1, picked=picked
            )
        if self.search:
            query = query.where(fuzzy(searched.text, self.search)).order_by(
                closeness(searched.text, self.search).desc()
            )
        found = _count(session, query) if self.search else total
        page = min(self.page, max(1, ceil(found / PER_PAGE)))
        rows = session.scalars(
            query.order_by(func.lower(searched.name), table.id)
            .limit(PER_PAGE)
            .offset((page - 1) * PER_PAGE)
        ).all()
        log.debug("listed", found=found, total=total, page=page)
        return replace(self, rows=rows, found=found, total=total, page=page)

    def suggest(
        self,
        session: Session,
        searched: Searched[T],
        query: Select,
        hint: Callable[[T], str] = lambda _row: "",
    ) -> list[dict[str, str]]:
        """What the search box offers for what is typed so far: the closest
        records the filter shows, each with its name, a hint to tell it
        apart, and the link that narrows the list to it."""
        if not self.search:
            return []
        query = self._shown(searched, query)
        rows = session.scalars(
            query.where(fuzzy(searched.text, self.search))
            .order_by(
                closeness(searched.text, self.search).desc(),
                func.lower(searched.name),
            )
            .limit(SUGGESTED)
        ).all()
        log.debug("suggested", found=len(rows))
        return [
            {
                "name": name,
                "hint": hint(row),
                "href": self.link(pick=getattr(row, "id"), search=""),  # noqa: B009
            }
            for row, name in zip(rows, searched.names(session, rows), strict=True)
        ]


def _count(session: Session, query: Select) -> int:
    return session.scalar(select(func.count()).select_from(query.subquery())) or 0
