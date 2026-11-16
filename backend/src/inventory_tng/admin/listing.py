"""Lists of records (docs/decisions/0019, Lists).

Every list page has a View: its sort, filters and page size, read from the
query string (View.read), which `ordered` applies to a select, or
`arranged` to rows built in Python; _listing.html draws its headings, its
"Show 25 · 50 · 100 · All", its filter form and its pager (Ali,
2026-11-15, inventory-tng-qwb).

A long list of records also has search that forgives typos, with
suggestions as one types, and "Current · Retired · All" (or another word
than Retired, such as labels' Inactive): its page says how to search its
kind (Searched) and reads its query string with Searched.read, and
_listing.html draws the Listing.

The query string carries it all, so every view is a link: `q`, the search;
`show`, which records; `pick`, the one record a suggestion picked, which
narrows the list to it; each filter by its own name; `sort` and `order`;
`per`, the page size; and `page`."""

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from math import ceil
from typing import Annotated, Any, Literal
from unicodedata import normalize
from urllib.parse import urlencode

import structlog
from fastapi import Depends
from sqlalchemy import (
    ColumnElement,
    Select,
    SQLColumnExpression,
    and_,
    case,
    func,
    not_,
    or_,
    select,
)
from sqlalchemy.orm import Session

from inventory_tng import units
from inventory_tng.models import (
    INTEGER_MAX,
    folds,
    fuzzy,
    holds,
    in_ascii_once,
    reading_rules,
    whole,
)

log = structlog.get_logger(__name__)

type Show = Literal["current", "retired", "inactive", "all"]
type Order = Literal["asc", "desc"]
# What the filter between Current and All shows: the records not current.
type Lapsed = Literal["retired", "inactive"]
# How many rows a page shows unless asked otherwise, and what it may be
# asked to show: None is All (Ali, 2026-11-15, inventory-tng-qwb).
PER_PAGE = 50
PER_PAGES: tuple[int | None, ...] = (25, 50, 100, None)
# How many suggestions the search box offers.
SUGGESTED = 8
# The longest search read: enough for any name, and no more work than that.
SEARCH_MAX = 200


def last_page(found: int, per_page: int | None) -> int:
    """How many pages of `per_page` (None: all on one) `found` records
    fill: one at least."""
    return 1 if per_page is None else max(1, ceil(found / per_page))


@dataclass(frozen=True)
class Sort:
    """One way a list sorts, by a heading: by SQL expressions, each after
    the one before, or by a Python key, for rows built in Python; and which
    way it sorts first, such as a date's newest first."""

    by: tuple[Any, ...] | Callable[[Any], Any]
    first: Order = "asc"

    @classmethod
    def of(cls, *expressions: SQLColumnExpression[Any], first: Order = "asc") -> Sort:
        return cls(expressions, first)

    @classmethod
    def keyed(cls, key: Callable[[Any], Any], first: Order = "asc") -> Sort:
        return cls(key, first)


@dataclass(frozen=True)
class Sorting:
    """How a list page may sort, by name as its query string says it, and
    the name of its own order, as it was before it could be sorted."""

    default: str
    columns: Mapping[str, Sort]


@dataclass(frozen=True)
class Asked:
    """How a list's query string asks for it to be shown, as typed: its
    sort, order, page size and page (View.read)."""

    sort: str = ""
    order: str = ""
    per: str = ""
    page: str = ""


# A list route's view, as its query string asks.
AskedView = Annotated[Asked, Depends()]
# Nothing asked: the list's own view.
UNASKED = Asked()


@dataclass(frozen=True)
class View:
    """How a list is shown, as its query string asks (View.read): sorted by
    one of `sorting`'s columns, or by its default (a search's best match
    first while searching); filtered; in pages of `per_page`, None for all;
    which page; and how many there are (counted). `kept` is the rest of the
    query string, such as a search, which every link keeps."""

    here: str
    sorting: Sorting
    sort: str | None = None
    descending: bool = False
    per_page: int | None = PER_PAGE
    page: int = 1
    searching: bool = False
    filters: tuple[tuple[str, str], ...] = ()
    kept: tuple[tuple[str, str], ...] = ()
    found: int = 0

    @classmethod
    def read(
        cls,
        here: str,
        sorting: Sorting,
        asked: Asked = UNASKED,
        *,
        searching: bool = False,
        filters: Mapping[str, str] | None = None,
        kept: Mapping[str, str] | None = None,
    ) -> View:
        """Anything odd is the default's, never an error, as a link may
        have been typed by hand: a sort not offered, the list's own order;
        an order not asc or desc, the column's first; a size not offered,
        PER_PAGE. Filters and kept names left blank are not said."""
        sort, order = asked.sort, asked.order
        column = sorting.columns.get(sort)
        if order not in ("asc", "desc"):
            order = column.first if column is not None else "asc"
        sized = {"all": None} | {str(n): n for n in PER_PAGES if n is not None}
        return cls(
            here,
            sorting,
            sort=sort if column is not None else None,
            descending=column is not None and order == "desc",
            per_page=sized.get(asked.per, PER_PAGE),
            page=whole(asked.page, 1, INTEGER_MAX) or 1,
            searching=searching,
            filters=_pairs(filters),
            kept=_pairs(kept),
        )

    @property
    def by(self) -> str | None:
        """The column the list is sorted by; None while searching unsorted,
        best match first."""
        if self.sort is not None:
            return self.sort
        return None if self.searching else self.sorting.default

    @property
    def desc(self) -> bool:
        if self.sort is not None:
            return self.descending
        return self.sorting.columns[self.sorting.default].first == "desc"

    def aria_sort(self, column: str) -> str | None:
        """A heading's aria-sort: only the sorted column's."""
        if column != self.by:
            return None
        return "descending" if self.desc else "ascending"

    @property
    def pages(self) -> int:
        return last_page(self.found, self.per_page)

    @property
    def offset(self) -> int:
        return 0 if self.per_page is None else (self.page - 1) * self.per_page

    def counted(self, found: int) -> View:
        """With how many there are, and its page within them."""
        return replace(
            self, found=found, page=min(self.page, last_page(found, self.per_page))
        )

    def fields(self, *dropped: str) -> list[tuple[str, str]]:
        """The query string's names and values, but those `dropped`: a
        form's hidden fields."""
        order = None
        if self.sort is not None:
            first = self.sorting.columns[self.sort].first
            mine: Order = "desc" if self.descending else "asc"
            order = None if mine == first else mine
        found = [
            *self.kept,
            *self.filters,
            ("sort", self.sort or ""),
            ("order", order or ""),
            ("per", "" if self.per_page == PER_PAGE else str(self.per_page or "all")),
            ("page", "" if self.page == 1 else str(self.page)),
        ]
        return [(name, value) for name, value in found if value and name not in dropped]

    def filtered(self, name: str) -> str:
        """What a filter is set to, blank if not."""
        return dict(self.filters).get(name, "")

    def unfiltered_fields(self) -> list[tuple[str, str]]:
        """A filter form's hidden fields: all but its filters, its page and
        a search's pick (Listing), which a new filter drops."""
        return self.fields("page", "pick", *(name for name, _ in self.filters))

    @property
    def sizes(self) -> tuple[int | None, ...]:
        return PER_PAGES

    @property
    def query_string(self) -> str:
        """This view's query string, with its "?", if any."""
        query = self.fields()
        return f"?{urlencode(query)}" if query else ""

    def link(self, **changed: object) -> str:
        """This view's address, `changed` (View's fields)."""
        return self.here + replace(self, **changed).query_string

    def sort_link(self, column: str) -> str:
        """A heading's link: sorted by its column the way it sorts first,
        or, if sorted by it already, the other way."""
        if column == self.by:
            return self.link(sort=column, descending=not self.desc, page=1)
        first = self.sorting.columns[column].first
        return self.link(sort=column, descending=first == "desc", page=1)

    def size_link(self, per_page: int | None) -> str:
        return self.link(per_page=per_page, page=1)

    def best_match_link(self) -> str:
        return self.link(sort=None, page=1)

    def unfiltered_link(self) -> str:
        return self.link(filters=(), page=1)


def _pairs(query: Mapping[str, str] | None) -> tuple[tuple[str, str], ...]:
    """The names and values said: those left blank are not."""
    return tuple((name, value) for name, value in (query or {}).items() if value)


def _directed(sort: Sort, descending: bool) -> list[Any]:
    assert isinstance(sort.by, tuple), "a select sorts by SQL"
    return [each.desc() if descending else each.asc() for each in sort.by]


def order(view: View, tiebreak: SQLColumnExpression[Any]) -> list[Any]:
    """The ORDER BY of a view: its column, then the list's own order, then
    `tiebreak`, such as the id, so a page always holds the same rows. Best
    match first, while searching unsorted, comes before these, in the
    select (Listing.find)."""
    sorting = view.sorting
    default = sorting.columns[sorting.default]
    found = []
    if view.by is not None:
        found += _directed(sorting.columns[view.by], view.desc)
    if view.by != sorting.default:
        found += _directed(default, default.first == "desc")
    found.append(tiebreak.desc() if view.desc else tiebreak.asc())
    return found


def ordered(query: Select, view: View, tiebreak: SQLColumnExpression[Any]) -> Select:
    """`query` in the view's order (order()), one page of it: the view
    counted first."""
    return (
        query.order_by(*order(view, tiebreak)).limit(view.per_page).offset(view.offset)
    )


def arranged[R](rows: Iterable[R], view: View) -> tuple[list[R], View]:
    """Rows built in Python, by Sort.keyed columns, in the view's order,
    then the list's own, then as they came (best match first, while
    searching unsorted); one page of them, and the view counted."""
    return paged(in_order(rows, view), view)


def in_order[R](rows: Iterable[R], view: View) -> list[R]:
    """Rows built in Python in the view's order, as arranged() puts them,
    all of them: such as the rows of each group on a page of groups."""
    found = list(rows)
    sorting = view.sorting
    if view.by is not None:
        default = sorting.columns[sorting.default]
        if view.by != sorting.default:
            found.sort(key=_key(default), reverse=default.first == "desc")
        found.sort(key=_key(sorting.columns[view.by]), reverse=view.desc)
    return found


def paged[R](rows: Iterable[R], view: View) -> tuple[list[R], View]:
    """One page of `rows`, in the order given, and the view counted: of
    groups, such as Stock's, the page size counts groups, so none is split
    across pages (Ali, 2026-11-15, inventory-tng-qwb)."""
    found = list(rows)
    view = view.counted(len(found))
    end = None if view.per_page is None else view.offset + view.per_page
    return found[view.offset : end], view


def _key(sort: Sort) -> Callable[[Any], Any]:
    assert callable(sort.by), "rows built in Python sort by a key"
    return sort.by


def count(session: Session, query: Select) -> int:
    """How many rows `query` finds."""
    return session.scalar(select(func.count()).select_from(query.subquery())) or 0


def tidied(typed: str) -> str:
    """What was typed in a search or filter box, its spaces tidied, no
    longer than SEARCH_MAX."""
    return " ".join(typed.split())[:SEARCH_MAX]


def folded(search: str, rules: units.Rules | None = None) -> tuple[str, ...]:
    """What was typed as what is searched is kept (models.folds): as typed,
    and with its units respelt if that differs, by `rules` first, each no
    longer than SEARCH_MAX, as folding may lengthen it."""
    return tuple(form[:SEARCH_MAX] for form in folds(search, rules))


def grouped(search: str, rules: units.Rules | None = None) -> list[tuple[str, ...]]:
    """What was typed in groups (units.groups), each folded as typed and
    respelt, as folds() folds them (models.in_ascii_once), and no longer
    than SEARCH_MAX; a group folding to nothing left out."""
    found = []
    for group in units.groups(normalize("NFKD", search), rules):
        if forms := tuple(f[:SEARCH_MAX] for f in in_ascii_once(*group) if f.strip()):
            found.append(forms)
    return found


def every_group(
    column: SQLColumnExpression[str], search: str, rules: units.Rules | None = None
) -> ColumnElement[bool] | None:
    """Whether `column` holds every group of what was typed (grouped), each
    as typed or respelt (Ali, 2026-11-15, inventory-tng-ist.1): so `100 ft
    48 V` finds `Cable 100 feet, PoE 48 VDC`, which holds neither form
    whole. None where that adds nothing: one group, which is the whole, or
    none, past units.GROUPS_MAX."""
    groups = grouped(search, rules)
    if len(groups) < 2:
        return None
    return and_(*(or_(*(holds(column, form) for form in g)) for g in groups))


def closeness(
    column: SQLColumnExpression[str],
    forms: Sequence[str],
    every: ColumnElement[bool] | None = None,
) -> ColumnElement[float]:
    """How close a match is, best highest: holding a form whole first (2),
    then holding every group (`every`, 1, Ali, 2026-11-15), then how alike
    it is to the closest form."""
    alike = func.greatest(
        *(
            func.greatest(func.similarity(column, f), func.word_similarity(f, column))
            for f in forms
        )
    )
    held = [(or_(*(holds(column, f) for f in forms)), 2.0)]
    if every is not None:
        held.append((every, 1.0))
    return case(*held, else_=0.0) + alike


@dataclass(frozen=True)
class Searched[T]:
    """How a list page, at `here`, finds its kind of record: the model, the
    text a search reads, folded (models.held), such as an item's
    search_text, its name, by which suggestions are sorted, and the
    headings its list sorts by (`sorting`). Optionally: the select of the model it reads,
    if joined to more; a column a search matches only whole, held in
    capitals, such as a label's code, put first; which are current, if not
    those with no retired_at; what the filter between Current and All calls
    the rest; and each row as the page shows it, with a `name`, if not the
    row itself. Its search reads units by the reading rules where the
    model's search_text does (models.Searchable.READS_RULES)."""

    here: str
    model: type[T]
    text: SQLColumnExpression[str]
    name: SQLColumnExpression[str]
    sorting: Sorting
    query: Select | None = None
    whole: SQLColumnExpression[str] | None = None
    current: ColumnElement[bool] | None = None
    lapsed: Lapsed = "retired"
    shown: Callable[[Session, Sequence[T]], Sequence[Any]] | None = None

    def read(
        self,
        q: str = "",
        show: str = "",
        pick: str = "",
        asked: Asked = UNASKED,
        *,
        filters: Mapping[str, str] | None = None,
        where: Sequence[ColumnElement[bool]] = (),
    ) -> Listing[T]:
        """The view of the list a query string asks for: anything odd is
        the plain list's, never an error, as a link may have been typed by
        hand. `filters` are the page's own, as its query string says them,
        and `where` what they pick."""
        shows: dict[str, Show] = {
            "current": "current",
            self.lapsed: self.lapsed,
            "all": "all",
        }
        search = tidied(q)
        # Nothing once folded, such as a lone accent: no search.
        search = search if any(f.strip() for f in folded(search)) else ""
        shown = shows.get(show, "current")
        picked = whole(pick, 1, INTEGER_MAX)
        view = View.read(
            self.here,
            self.sorting,
            asked,
            searching=bool(search),
            filters=filters,
            kept=_kept(search, shown, picked),
        )
        return Listing(self, view, search, shown, picked, tuple(where))

    def base(self, show: Show) -> Select:
        """The records `show` asks for."""
        query = select(self.model) if self.query is None else self.query
        if show == "all":
            return query
        current = self.is_current()
        return query.where(current if show == "current" else not_(current))

    def rules(self, session: Session) -> units.Rules | None:
        reads = getattr(self.model, "READS_RULES", False)
        return reading_rules(session) if reads else None

    def matching(
        self, search: str, rules: units.Rules | None = None
    ) -> ColumnElement[bool]:
        forms, every = folded(search, rules), every_group(self.text, search, rules)
        return self._matching(search, forms, every)

    def searching(
        self,
        query: Select,
        search: str,
        rules: units.Rules | None = None,
        *,
        best_first: bool = True,
    ) -> Select:
        """`query` narrowed to the records matching `search`, closest
        first unless not `best_first`: what was typed read once for
        both."""
        forms, every = folded(search, rules), every_group(self.text, search, rules)
        query = query.where(self._matching(search, forms, every))
        if not best_first:
            return query
        return query.order_by(self._closeness(search, forms, every).desc())

    def _matching(
        self, search: str, forms: Sequence[str], every: ColumnElement[bool] | None
    ) -> ColumnElement[bool]:
        found = or_(*(fuzzy(self.text, form) for form in forms))
        if every is not None:
            found = or_(found, every)
        if self.whole is None:
            return found
        return or_(found, self.whole == search.upper())

    def _closeness(
        self, search: str, forms: Sequence[str], every: ColumnElement[bool] | None
    ) -> ColumnElement[float]:
        """As closeness(), and the whole column matched before anything."""
        near = closeness(self.text, forms, every)
        if self.whole is None:
            return near
        return case((self.whole == search.upper(), 3.0), else_=0.0) + near

    def is_current(self) -> ColumnElement[bool]:
        table: Any = self.model
        return table.retired_at.is_(None) if self.current is None else self.current

    def as_shown(self, session: Session, rows: Sequence[T]) -> Sequence[Any]:
        return rows if self.shown is None else self.shown(session, rows)


def _kept(search: str, show: Show, pick: int | None) -> dict[str, str]:
    """A search's part of the query string, which its view keeps."""
    return {
        "q": search,
        "show": "" if show == "current" else show,
        "pick": "" if pick is None else str(pick),
    }


@dataclass(frozen=True)
class Listing[T]:
    """One view of a long list, as its query string asks for it
    (Searched.read), and what it finds there."""

    searched: Searched[T]
    view: View
    search: str = ""
    show: Show = "current"
    pick: int | None = None
    # What the page's filters pick (Searched.read).
    where: tuple[ColumnElement[bool], ...] = ()
    rows: Sequence[T] = ()
    # The rows as the page shows them (Searched.shown).
    shown: Sequence[Any] = ()
    # How many the filter has, unsearched.
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
    def found(self) -> int:
        """How many the view finds."""
        return self.view.found

    @property
    def narrowed(self) -> bool:
        """Whether a search or a pick narrows the list."""
        return bool(self.search) or self.pick is not None

    @property
    def suggest_address(self) -> str:
        """Where the search box asks for suggestions: of the records the
        filters show, each leading to its page size, as Find keeps it."""
        query = self.view.fields("q", "pick", "sort", "order", "page")
        return f"{self.here}/suggest" + (f"?{urlencode(query)}" if query else "")

    def link(
        self,
        *,
        show: Show | None = None,
        page: int | None = None,
        search: str | None = None,
        pick: int | None = None,
    ) -> str:
        """This view's address, changed: a new filter, search or pick starts
        at page 1, and a pick stays only while just the page changes. Its
        sort, page size and the page's own filters stay."""
        if show is None and search is None and pick is None:
            return self.view.link(page=page or self.view.page)
        search = self.search if search is None else search
        kept = _kept(search, show or self.show, pick)
        return self.view.link(kept=_pairs(kept), searching=bool(search), page=page or 1)

    def find(self, session: Session) -> Listing[T]:
        """This view's rows: filtered, searched or picked, sorted (best
        match first while searching unsorted), one page of them."""
        searched = self.searched
        table: Any = searched.model
        query = searched.base(self.show).where(*self.where)
        total = count(session, query)
        if self.pick is not None:
            # One record at most: no count, nor a page, to work out.
            rows = session.scalars(query.where(table.id == self.pick)).all()
            shown = searched.as_shown(session, rows)
            return replace(
                self,
                view=replace(self.view, page=1, found=len(rows)),
                rows=rows,
                shown=shown,
                total=total,
                picked=shown[0].name if shown else None,
            )
        if self.search:
            query = searched.searching(
                query,
                self.search,
                searched.rules(session),
                best_first=self.view.by is None,
            )
        view = self.view.counted(count(session, query) if self.search else total)
        rows = session.scalars(ordered(query, view, table.id)).all()
        log.debug(
            "listed",
            found=view.found,
            total=total,
            page=view.page,
            sort=view.by,
            per_page=view.per_page,
        )
        shown = searched.as_shown(session, rows)
        return replace(self, view=view, rows=rows, shown=shown, total=total)

    def suggest(
        self, session: Session, hint: Callable[[T], str] = lambda _row: ""
    ) -> list[dict[str, str]]:
        """What the search box offers for what is typed so far: the closest
        records the filters show, each with its name, a hint to tell it
        apart, and the link that narrows the list to it."""
        if not self.search:
            return []
        searched = self.searched
        rules = searched.rules(session)
        query = searched.base(self.show).where(*self.where)
        rows = session.scalars(
            searched.searching(query, self.search, rules)
            .order_by(func.lower(searched.name))
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
