"""A volunteer's batch, as the scan page sends it (scan/batch_api.py),
written through the ledger's core (stock/ledger.py, docs/decisions/0003).

The scan page sends a batch with a key it made; a batch sent again with the
same key, after a dropped connection or from the outbox, is written once
(record). What is wrong with one is said line by line, so a volunteer is
told which line to fix. An opening's packages whose items track them become
open packages (stock/open_packages.py). Imports run one way, from scan to
stock: nothing in stock imports this or its schemas (scan/batch_schemas.py).
"""

import uuid
from collections import Counter, defaultdict
from collections.abc import Collection, Mapping, Sequence
from decimal import Decimal
from types import MappingProxyType
from typing import Literal, NamedTuple, cast

import structlog
from opentelemetry import metrics
from sqlalchemy import select
from sqlalchemy.orm import Session

from inventory_tng.core.db import RuleBroken
from inventory_tng.models.catalogue import ItemPackage
from inventory_tng.models.stock import LedgerTransaction, Movement, OpenPackage
from inventory_tng.models.volunteers import Volunteer
from inventory_tng.scan import batch_schemas
from inventory_tng.stock import open_packages
from inventory_tng.stock.ledger import (
    LOOSE,
    Entry,
    Form,
    Held,
    OpenFacts,
    PackageFacts,
    form_breaks,
    hold,
    hold_forms,
    insert_transaction,
    lines_break,
    repeated,
)

log = structlog.get_logger(__name__)
meter = metrics.get_meter(__name__)

replayed = meter.create_counter(
    "inventory.transactions.replayed",
    unit="{transaction}",
    description="Batches sent again with a key already written, by kind",
)
refused = meter.create_counter(
    "inventory.transactions.refused",
    unit="{transaction}",
    # FastAPI's own 422s, for a batch that cannot be at all, are a client's
    # bug and are not counted here.
    description="Batches refused for naming rows that do not exist, for "
    "a package resized since the page read it, or for reusing a key, by reason",
)


class BatchRefused(Exception):
    def __init__(self, problems: list[batch_schemas.Problem]) -> None:
        super().__init__(f"{len(problems)} problems")
        self.problems = problems


class KeyReused(Exception):
    """The key was already used, for a different batch: `recorded`, the
    transaction under it, as a batch (None if no volunteer's batch wrote
    it); `over`, by movement, how much more of it was recorded than this
    batch sends, of what it took off since it was put back (_more_written),
    for an administrator to correct (stock/over_recorded.py)."""

    def __init__(
        self, recorded: batch_schemas.RecordedBatch | None, over: Mapping[int, Decimal]
    ) -> None:
        super().__init__("key reused")
        self.recorded = recorded
        self.over = over


Side = Literal["from", "to"]


def retired_sides(
    from_id: int | None, to_id: int | None, places: Mapping[int, bool]
) -> list[Side]:
    """The sides of a batch whose place `places` (by id, whether retired)
    says is retired."""
    return [
        side
        for side, location_id in (("from", from_id), ("to", to_id))
        if location_id is not None and places.get(location_id, False)
    ]


class Recorded(NamedTuple):
    transaction: LedgerTransaction
    # Whether this call wrote it, and if so, the sides at a retired place.
    written: bool
    retired: Sequence[Side] = ()
    # How many packages an opening opened, by what they became.
    opened: Mapping[Into, int] = MappingProxyType({})
    # How many open packages it reported used up, by how.
    reported: Mapping[Reported, int] = MappingProxyType({})
    # How many checks of what an open package has it raised, by how.
    checks: Mapping[open_packages.CheckRaised, int] = MappingProxyType({})


def entry_of(line: batch_schemas.Line, kind: str, opened: int | None = None) -> Entry:
    """A batch's line as the ledger writes it: sealed on each side `kind`
    has, loose outside. An opening's leaves sealed and arrives as the open
    package `opened`, or loose."""
    sealed = Form(package_id=line.package_id)
    if kind == "open":
        arrives = Form(open_package_id=opened) if opened else LOOSE
        return Entry(line.item_id, line.quantity, sealed, arrives)
    if opened is not None:
        # Taken from an open package: cut loose, or moved whole.
        taken = Form(open_package_id=opened)
        return Entry(line.item_id, line.quantity, taken, taken if line.whole else LOOSE)
    has_from, has_to = batch_schemas.SIDES[kind]
    return Entry(
        line.item_id,
        line.quantity,
        sealed if has_from else LOOSE,
        sealed if has_to else LOOSE,
    )


def record(
    session: Session, batch: batch_schemas.Batch, *, device_id: int | None
) -> Recorded:
    """Write the batch, or find the one already written with its key. The
    caller commits.

    A batch already written is not checked again: the catalogue may have
    changed since, and the volunteer must not be asked to fix what is saved."""
    if (written := _replay(session, batch)) is not None:
        return Recorded(written, False)
    used_up = batch.used_up or []
    lengths = batch.lengths or []
    header = {
        "device_id": device_id,
        **_header(batch),
        "used_up": used_up,
        "lengths": _lengths_as_kept(lengths),
    }
    # An opening's are made later (_opening); another's are taken from.
    taken = {} if batch.kind == "open" else _open_packages_named(session, batch)
    lines = [
        entry_of(line, batch.kind, taken.get(number))
        for number, line in enumerate(batch.lines)
    ]
    held = hold(session, header, [line.item_id for line in lines])
    length_keys = [each.open_package_key for each in lengths]
    # Read together: the open packages said used up, and those measured.
    keyed = _keyed(session, {*used_up, *length_keys})
    reports = {key: keyed[key] for key in used_up if key in keyed}
    measured = {key: keyed[key] for key in length_keys if key in keyed}
    # Those reported too, with the lines', in one order (stock/ledger.py's).
    packages, opens, rows = hold_forms(
        session, lines, also={*reports.values(), *measured.values()}
    )
    # Its open packages' locks waited for any request writing it, which a
    # form rule would refuse, as it changed them: found as written instead.
    if opens and (written := _replay(session, batch)) is not None:
        return Recorded(written, False)
    problems = _problems(session, batch, held, lines, packages, opens)
    problems += [
        (("lines", number, "open_package_key"), "not_found")
        for number, line in enumerate(batch.lines)
        if batch.kind != "open"
        and line.open_package_key is not None
        and number not in taken
    ]
    if batch.kind != "open":
        twice = repeated([line.open_package_key for line in batch.lines])
        problems += [
            (("lines", number, "open_package_key"), "repeated") for number in twice
        ]
    problems += [(("used_up", number), "repeated") for number in repeated(used_up)]
    whole = {line.open_package_key for line in batch.lines if line.whole}
    for number, key in enumerate(used_up):
        opened = opens.get(reports.get(key, 0))
        if opened is None:
            problems.append((("used_up", number), "not_found"))
        elif opened.location_id != batch.from_location_id:
            problems.append((("used_up", number), "open_package_elsewhere"))
        elif key in whole:
            problems.append((("used_up", number), "moved_whole"))
    twice = repeated(length_keys)
    for number, key in enumerate(length_keys):
        at: tuple[str | int, ...] = ("lengths", number, "open_package_key")
        opened = opens.get(measured.get(key, 0))
        if number in twice:
            problems.append((at, "repeated"))
        elif opened is None:
            problems.append((at, "not_found"))
        elif opened.location_id != batch.from_location_id:
            problems.append((at, "open_package_elsewhere"))
    if problems:
        kinds = {kind for _, kind in problems}
        # Counted apart: how often a page's catalogue is only a resize behind.
        reason = "package_resized" if kinds == {"package_resized"} else "invalid"
        refused.add(1, {"reason": reason})
        log.info("batch refused", problems=sorted(kinds))
        raise BatchRefused(problems)
    try:
        lines, opened = _opening(session, batch, packages, lines)
        transaction = insert_transaction(session, batch.key, header, lines)
    except RuleBroken as broken:
        # A rule _problems could not see: the catalogue changed since the
        # page's, as a package's setting, or a key used elsewhere. Said, so
        # the outbox shows it rather than sending it again and again.
        refused.add(1, {"reason": "invalid"})
        log.info("batch refused", rule=broken.rule)
        raise BatchRefused([(("lines",), "refused")]) from broken
    if transaction is None:
        written = _replay(session, batch)
        assert written is not None  # The conflict was on this key.
        return Recorded(written, False)
    retired = retired_sides(batch.from_location_id, batch.to_location_id, held.places)
    reported = open_packages.report(
        session, reports.values(), batch.volunteer_id, transaction.created_at
    )
    # Each open package cut from: what it showed before, and after.
    cuts: defaultdict[int, Decimal] = defaultdict(Decimal)
    for number, open_id in taken.items():
        if not batch.lines[number].whole:
            cuts[open_id] += batch.lines[number].quantity
    checks: dict[open_packages.CheckRaised, int] = {
        "volunteer": sum(
            open_packages.report_length(
                rows[measured[each.open_package_key]],
                each.left,
                batch.volunteer_id,
                transaction,
            )
            for each in lengths
        ),
        "below_zero": sum(
            open_packages.went_below_zero(
                rows[open_id], opens[open_id].left, cut, transaction.created_at
            )
            for open_id, cut in cuts.items()
        ),
    }
    # Said with a cut from it, or alone, taking nothing from it.
    cut = {line.open_package_key for line in batch.lines}
    how: dict[Reported, int] = {}
    for key, open_id in reports.items():
        if open_id in reported:
            way: Reported = "cut" if key in cut else "button"
            how[way] = how.get(way, 0) + 1
    return Recorded(transaction, True, retired, opened, how, checks)


# What an opened package became: an open package, followed, or loose units.
Into = Literal["open", "loose"]
# How an open package was reported used up.
Reported = Literal["cut", "button"]


def _packages_in(line: batch_schemas.Line, size: Decimal) -> int:
    """How many whole packages of `size` a line holds."""
    return int(line.quantity / size)


def _opening_keys(
    batch: batch_schemas.Batch, number: int, count: int
) -> list[uuid.UUID]:
    """The keys an opening's line makes its open packages under, one for
    each of the `count` packages it opens: the page's, for its one, or made from
    the batch's, so they are the same however often sent. A line of several
    is a page's that thought the package not followed (_opening)."""
    line = batch.lines[number]
    if line.open_package_key is not None:
        return [line.open_package_key]
    return [
        uuid.uuid5(batch.key, f"open {number} {each}" if each else f"open {number}")
        # A page never sent more: a replay of more is another batch's.
        for each in range(min(count, batch_schemas.MAX_LINES))
    ]


def _opening(
    session: Session,
    batch: batch_schemas.Batch,
    packages: Mapping[int, PackageFacts],
    lines: list[Entry],
) -> tuple[list[Entry], dict[Into, int]]:
    """A batch's lines (`lines`, as entry_of makes them) as the ledger
    writes them: an opening's packages its items track opened become open
    packages (open_packages.make), one for each, however many a line opens,
    as the page may have thought them not followed; and the rest loose
    units, one line for each package however many lines the page sent, as
    it may have thought them followed. And how many packages it opened, by
    what each became."""
    if batch.kind != "open":
        return lines, {}
    opened: dict[Into, int] = {}
    made: dict[int, list[int]] = {}
    for number, line in enumerate(batch.lines):
        assert line.package_id is not None  # Batch's validator.
        package = packages[line.package_id]
        into: Into = "open" if package.tracked else "loose"
        count = _packages_in(line, package.size)
        opened[into] = opened.get(into, 0) + count
        if package.tracked:
            where = batch.from_location_id or 0
            ids = [
                open_packages.make(session, key, line.package_id, where).id
                for key in _opening_keys(batch, number, count)
            ]
            if len(ids) > 1:
                log.info(
                    "opening line split into open packages",
                    item_package_id=line.package_id,
                    packages=len(ids),
                )
            made[number] = ids
    return _as_opened(batch, made), opened


def _as_opened(
    batch: batch_schemas.Batch, made: Mapping[int, Sequence[int]]
) -> list[Entry]:
    """An opening's lines, into the open packages `made` for a line by its
    number, if any, a line for each and its share; the others loose, one
    line for each package."""
    written: list[Entry] = []
    loose: dict[int | None, Entry] = {}
    for number, line in enumerate(batch.lines):
        if ids := made.get(number):
            share = line.quantity / len(ids)
            written += [
                entry_of(line, batch.kind, opened)._replace(quantity=share)
                for opened in ids
            ]
        elif (before := loose.get(line.package_id)) is None:
            loose[line.package_id] = entry_of(line, batch.kind)
        else:
            loose[line.package_id] = before._replace(
                quantity=before.quantity + line.quantity
            )
    return written + list(loose.values())


def _line_form_breaks(
    kind: str,
    line: Entry,
    places: tuple[int | None, int | None],
    packages: Mapping[int, PackageFacts],
    opens: Mapping[int, OpenFacts],
    *,
    keyed: bool,
) -> list[str]:
    """form_breaks' rules a batch's line breaks, before an opening's open
    packages are made: one opened into one under the page's key, if its
    item tracks them, is one whole package (open_package_full). One without
    a key, from a page that thought them loose, may be several (_opening)."""
    package = packages.get(line.leaves.package_id or 0)
    if kind != "open" or package is None or not package.tracked:
        return form_breaks(kind, line, places, packages, opens)
    sealed = Entry(line.item_id, line.quantity, line.leaves, line.leaves)
    broken = form_breaks("move", sealed, places, packages, {})
    if keyed and line.quantity != package.size:
        broken.append("open_package_full")
    return broken


# Each of the rules a batch's line can break, as its problem: which field,
# and what. A batch's forms are sealed or loose alike on both sides
# (entry_of), or an opening's, so no other form rule can be broken by one.
LINE_PROBLEMS: dict[str, tuple[str, batch_schemas.ProblemKind]] = {
    "movement_whole_units": ("quantity", "not_whole"),
    "receipt_retired_item": ("item_id", "retired"),
    "movement_package_of_item": ("package_id", "not_found"),
    "movement_whole_packages": ("quantity", "not_whole_packages"),
    "receipt_retired_package": ("package_id", "retired_package"),
    "open_package_full": ("quantity", "one_package"),
    "opening_from_sealed": ("package_id", "not_found"),
    "movement_open_package_of_item": ("open_package_key", "not_found"),
    "movement_open_package_unopened": ("open_package_key", "not_found"),
    "movement_open_package_closed": ("open_package_key", "open_package_closed"),
    "movement_open_package_place": ("open_package_key", "open_package_elsewhere"),
    "movement_open_package_whole": ("quantity", "open_package_whole"),
}


def _as_counted(
    line: batch_schemas.Line, packages: Mapping[int, PackageFacts]
) -> PackageFacts | None:
    """The package a line names, in the size it was counted in, if the
    package is of its item: no quantity in another size says what the
    volunteer meant (package_resized)."""
    package = packages.get(line.package_id or 0)
    if package is None or package.item_id != line.item_id:
        return package
    assert line.package_size is not None  # Line's validator: with package_id.
    return package._replace(size=line.package_size)


def _packages_opened(
    batch: batch_schemas.Batch, packages: Mapping[int, PackageFacts]
) -> int:
    """How many open packages an opening would make (_opening), in the
    size each line was counted in (_as_counted)."""
    return sum(
        _packages_in(line, package.size)
        for line in batch.lines
        if (package := _as_counted(line, packages)) is not None and package.tracked
    )


def _problems(
    session: Session,
    batch: batch_schemas.Batch,
    held: Held,
    lines: Sequence[Entry],
    packages: Mapping[int, PackageFacts],
    opens: Mapping[int, OpenFacts],
) -> list[batch_schemas.Problem]:
    """What is wrong with a batch, line by line (`lines`, its own), with its
    rows held (hold)."""
    problems: list[batch_schemas.Problem] = []
    if session.get(Volunteer, batch.volunteer_id) is None:
        problems.append((("volunteer_id",), "not_found"))
    for field in ("from_location_id", "to_location_id"):
        location_id = getattr(batch, field)
        if location_id is not None and location_id not in held.places:
            problems.append(((field,), "not_found"))
    twice = repeated(
        [
            (line.item_id, line.leaves, line.arrives, sent.open_package_key)
            for line, sent in zip(lines, batch.lines, strict=True)
        ]
    )
    places = (batch.from_location_id, batch.to_location_id)
    for number, broken in enumerate(lines_break(batch.kind, held.items, lines)):
        sent = batch.lines[number]
        if number in twice:
            problems.append((("lines", number, "item_id"), "repeated"))
        if broken is None:
            problems.append((("lines", number, "item_id"), "not_found"))
            continue
        # Resized since the page read its catalogue: refused, and its rules
        # read in the size it was counted in.
        facts = packages
        counted = _as_counted(sent, packages)
        if counted is not None and counted != packages[sent.package_id or 0]:
            problems.append((("lines", number, "package_size"), "package_resized"))
            facts = {**packages, sent.package_id or 0: counted}
        broken += _line_form_breaks(
            batch.kind,
            lines[number],
            places,
            facts,
            opens,
            keyed=sent.open_package_key is not None,
        )
        for rule in dict.fromkeys(broken):
            field, kind = LINE_PROBLEMS[rule]
            problems.append((("lines", number, field), kind))
    # Never more open packages than a page could have sent a line each for.
    if (
        batch.kind == "open"
        and _packages_opened(batch, packages) > batch_schemas.MAX_LINES
    ):
        problems += [
            (("lines", number, "quantity"), "one_package")
            for number, line in enumerate(batch.lines)
            if line.open_package_key is None
        ]
    # Two rules may say one thing of one field: said once.
    return list(dict.fromkeys(problems))


def _header(batch: batch_schemas.Batch) -> dict[str, object]:
    """The batch's own columns: everything but its key, lines and reports,
    which are compared apart (_replay), and what it was changed from."""
    return batch.model_dump(
        exclude={"key", "lines", "used_up", "lengths", "changed_from"}
    )


def _lengths_as_kept(
    lengths: Sequence[batch_schemas.LengthReport],
) -> list[dict[str, str]]:
    """A batch's reports of what is on open packages, as its transaction
    keeps them."""
    return [
        {"open_package_key": str(each.open_package_key), "left": str(each.left)}
        for each in lengths
    ]


def _kept_as_sent(kept: Mapping[str, str]) -> tuple[uuid.UUID, Decimal]:
    """One of those, as sent, compared whatever its decimal places."""
    return uuid.UUID(kept["open_package_key"]), Decimal(kept["left"])


def _replay(session: Session, batch: batch_schemas.Batch) -> LedgerTransaction | None:
    """The transaction already written with the batch's key, if any, and
    KeyReused if it is not this batch."""
    transaction = session.scalar(
        select(LedgerTransaction).where(LedgerTransaction.idempotency_key == batch.key)
    )
    if transaction is None:
        return None
    rows = session.execute(
        select(
            Movement.id,
            Movement.item_id,
            Movement.quantity,
            Movement.from_package_id,
            Movement.from_open_package_id,
            Movement.to_package_id,
            Movement.to_open_package_id,
        ).where(Movement.transaction_id == transaction.id)
    ).all()
    # Each line as written, by its movement.
    movements = {
        movement_id: Entry(
            item_id, quantity, Form(from_package, from_open), Form(to_package, to_open)
        )
        for movement_id, item_id, quantity, from_package, from_open, to_package, to_open in rows
    }
    # As many of each line, whatever their order: an item may be on several
    # lines, in different forms.
    written = Counter(movements.values())
    sent_lines = _written_as(session, batch)
    sent = Counter(sent_lines)
    header = _header(batch)
    same_header = all(
        getattr(transaction, field) == value for field, value in header.items()
    )
    # Its reports as sent, whatever their order: what became of the open
    # packages since, as cleared by an administrator, does not count.
    same = (
        written == sent
        and Counter(transaction.used_up) == Counter(batch.used_up or [])
        and Counter(map(_kept_as_sent, transaction.lengths))
        == Counter((each.open_package_key, each.left) for each in batch.lengths or [])
        and same_header
    )
    if not same:
        refused.add(1, {"reason": "key_reused"})
        log.warning("idempotency key reused", transaction_id=transaction.id)
        # Stock moves by kind and places alone: who, or an install's number,
        # changed, the lines are still compared.
        moved_alike = all(
            getattr(transaction, field) == header[field]
            for field in ("kind", "from_location_id", "to_location_id")
        )
        # Only what this batch says it took off since it was put back
        # (changed_from) is the volunteer's: a batch from before another
        # tab's change, sent after it, takes off nothing.
        started = (
            None
            if batch.changed_from is None
            else _written_as(
                session, batch.model_copy(update={"lines": batch.changed_from})
            )
        )
        raise KeyReused(
            _as_batch(session, transaction, movements),
            _more_written(movements, sent_lines if moved_alike else [], started),
        )
    replayed.add(1, {"kind": batch.kind})
    log.info("transaction replayed", transaction_id=transaction.id)
    return transaction


def _more_written(
    written: Mapping[int, Entry],
    sent: Sequence[Entry],
    started: Sequence[Entry] | None,
) -> dict[int, Decimal]:
    """By movement, how much more of it was written than `sent` holds of
    the same item in the same forms, but no more than `sent` took off what
    it `started` from: what the volunteer took off a batch already
    recorded. Nothing without `started`, as then what differs may be
    another tab's change, not this batch's."""
    if started is None:
        return {}

    def tally(lines: Sequence[Entry]) -> defaultdict[tuple[int, Form, Form], Decimal]:
        by: defaultdict[tuple[int, Form, Form], Decimal] = defaultdict(Decimal)
        for line in lines:
            by[line.item_id, line.leaves, line.arrives] += line.quantity
        return by

    sent_by, started_by = tally(sent), tally(started)
    # By form, then shared out over its movements: two lines of one item in
    # the same forms (a batch may send them) take off what they hold
    # together, once.
    left = {
        form: min(written_quantity, started_by[form]) - sent_by[form]
        for form, written_quantity in tally(list(written.values())).items()
    }
    over = {}
    for movement_id, line in written.items():
        form = line.item_id, line.leaves, line.arrives
        more = min(line.quantity, left[form])
        if more > 0:
            over[movement_id] = more
            left[form] -= more
    return over


def _as_batch(
    session: Session, transaction: LedgerTransaction, movements: Mapping[int, Entry]
) -> batch_schemas.RecordedBatch | None:
    """`transaction`, written with `movements`, as a batch would send it:
    a sealed line by its package, at its size now; one taken from an open
    package, or an opening's, by the open package's key. None if no
    volunteer's batch wrote it."""
    if transaction.kind not in batch_schemas.SIDES or transaction.volunteer_id is None:
        return None
    lines = list(movements.values())
    forms = [form for line in lines for form in (line.leaves, line.arrives)]
    keys = _keys_of(session, {f.open_package_id for f in forms if f.open_package_id})
    sizes = _sizes(session, {f.package_id for f in forms if f.package_id})

    def line_of(line: Entry) -> batch_schemas.RecordedLine:
        if transaction.kind == "open":
            # Sealed, opened into a followed open package or loose.
            package_id = line.leaves.package_id
            open_id = line.arrives.open_package_id
            whole = None
        elif line.leaves.open_package_id is not None:
            package_id = None
            open_id = line.leaves.open_package_id
            whole = True if line.arrives.open_package_id == open_id else None
        else:
            package_id = line.leaves.package_id or line.arrives.package_id
            open_id = None
            whole = None
        return batch_schemas.RecordedLine(
            item_id=line.item_id,
            quantity=line.quantity,
            package_id=package_id,
            package_size=sizes.get(package_id) if package_id is not None else None,
            open_package_key=keys.get(open_id) if open_id is not None else None,
            whole=whole,
        )

    return batch_schemas.RecordedBatch(
        transaction_id=transaction.id,
        kind=cast(batch_schemas.VolunteerKind, transaction.kind),
        volunteer_id=transaction.volunteer_id,
        from_location_id=transaction.from_location_id,
        to_location_id=transaction.to_location_id,
        install_number=transaction.install_number,
        lines=[line_of(line) for line in lines],
        used_up=list(transaction.used_up),
        lengths=[
            batch_schemas.RecordedLength(open_package_key=key, left=left)
            for key, left in map(_kept_as_sent, transaction.lengths)
        ],
    )


def _keyed(session: Session, keys: Collection[uuid.UUID]) -> dict[uuid.UUID, int]:
    """The open packages made under these keys, by key."""
    if not keys:
        return {}
    found = session.execute(
        select(OpenPackage.key, OpenPackage.id).where(OpenPackage.key.in_(list(keys)))
    )
    return {key: open_id for key, open_id in found}


def _keys_of(session: Session, open_ids: Collection[int]) -> dict[int, uuid.UUID]:
    """These open packages' keys, by id: _keyed the other way."""
    if not open_ids:
        return {}
    found = session.execute(
        select(OpenPackage.id, OpenPackage.key).where(OpenPackage.id.in_(open_ids))
    )
    return {open_id: key for open_id, key in found}


def _sizes(session: Session, package_ids: Collection[int]) -> dict[int, Decimal]:
    """These packages' sizes, by id."""
    if not package_ids:
        return {}
    found = session.execute(
        select(ItemPackage.id, ItemPackage.size).where(ItemPackage.id.in_(package_ids))
    )
    return {package_id: size for package_id, size in found}


def _open_packages_named(
    session: Session, batch: batch_schemas.Batch
) -> dict[int, int]:
    """The open packages a batch's lines take from, by key, by line number.
    A key with none is left out."""
    keys = {
        number: line.open_package_key
        for number, line in enumerate(batch.lines)
        if line.open_package_key is not None
    }
    found = _keyed(session, keys.values())
    # Every line naming one, so two naming it are both found (and refused
    # as naming it twice).
    return {number: found[key] for number, key in keys.items() if key in found}


def _made_by_opening(
    session: Session, batch: batch_schemas.Batch
) -> dict[int, list[int]]:
    """The open packages an opening's lines made, under their keys
    (_opening_keys), by line number."""
    sizes = _sizes(
        session,
        {
            line.package_id
            for line in batch.lines
            if line.open_package_key is None and line.package_id is not None
        },
    )
    keys: dict[int, list[uuid.UUID]] = {}
    for number, line in enumerate(batch.lines):
        if line.open_package_key is not None:
            keys[number] = _opening_keys(batch, number, 1)
        # A package that is not there made none: the key was another batch's.
        elif (size := sizes.get(line.package_id or 0)) is not None:
            keys[number] = _opening_keys(batch, number, _packages_in(line, size))
    found = _keyed(session, [key for each in keys.values() for key in each])
    return {
        number: [found[key] for key in each if key in found]
        for number, each in keys.items()
    }


def _written_as(session: Session, batch: batch_schemas.Batch) -> list[Entry]:
    """A batch's lines as written once, to compare with what was: an
    opening's with the open packages made under their keys (_opening),
    another's taken from the open packages they name."""
    if batch.kind == "open":
        return _as_opened(batch, _made_by_opening(session, batch))
    named = _open_packages_named(session, batch)
    return [
        entry_of(line, batch.kind, named.get(number))
        for number, line in enumerate(batch.lines)
    ]
