"""What the batch endpoint (scan/batch_api.py) takes and answers: a
volunteer's batch and its lines, the problems of one refused, and the
transaction recorded under a key reused (docs/decisions/0003). The batch's
rules (scan/batch.py) read a Batch; this imports nothing of them, nor of
the ledger.
"""

import uuid
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, Field, ValidationInfo, field_validator, model_validator
from pydantic_core import PydanticCustomError

from inventory_tng.models.base import INTEGER_MAX, QUANTITY_PRECISION, QUANTITY_SCALE

# What a volunteer records. Counts and adjustments are an administrator's
# (inventory-tng-8i4.14). Whether each kind has a from side and a to side; the
# database's ck_ledger_transaction_sides says the same.
VolunteerKind = Literal["move", "receipt", "install", "open", "report"]
SIDES: dict[str, tuple[bool, bool]] = {
    "move": (True, True),
    "receipt": (False, True),
    "install": (True, False),
    # At one place: from and to are the same (docs/decisions/0020).
    "open": (True, True),
    # Nothing moves: the place the open packages reported used up are at.
    "report": (True, False),
}
MAX_LINES = 500
# Longer than any unit an item may have (models.catalogue.UNITS).
UNIT_MAX = 32

# An id or install number: positive, and fits PostgreSQL's integer.
Number = Annotated[int, Field(gt=0, le=INTEGER_MAX)]
# An amount in an item's unit, as the ledger's quantities are kept; one
# left on something may be none.
Amount = Annotated[
    Decimal,
    Field(gt=0, max_digits=QUANTITY_PRECISION, decimal_places=QUANTITY_SCALE),
]
Remaining = Annotated[
    Decimal,
    Field(ge=0, max_digits=QUANTITY_PRECISION, decimal_places=QUANTITY_SCALE),
]


class Line(BaseModel):
    item_id: Number
    # In the item's unit: the scan page turns packages into units first.
    quantity: Amount
    package_id: Annotated[
        Number | None,
        Field(
            description="Sealed packages of this package of the item, "
            "kept sealed (docs/decisions/0020): `quantity` is a whole "
            "number of them, in units. None: loose units. An opening's "
            "lines name the package opened."
        ),
    ] = None
    package_size: Annotated[
        Amount | None,
        Field(
            description="Required when `package_id` is given, and only allowed "
            "then: the size, in the item's unit, the package was counted in, "
            "from the catalogue. Left out with `package_id`, it is refused as "
            "package_size_required; given without, as "
            "package_size_without_package. A line of a package resized since "
            "is refused (package_resized), never counted in the new size.",
            # Checked when left out too.
            validate_default=True,
        ),
    ] = None
    open_package_key: Annotated[
        uuid.UUID | None,
        Field(
            description="An opening's: the key of the open package made, if "
            "its item tracks opened packages, made by the page so a later "
            "line can name it. One package a line, then. Any other line's: "
            "the open package it is taken from, cut loose, or moved whole."
        ),
    ] = None
    whole: Annotated[
        bool | None,
        Field(
            description="A move's line taken from an open package: all that "
            "is left on it, `quantity`, moves with it, and it stays open there."
        ),
    ] = None
    unit: Annotated[
        str,
        Field(
            min_length=1,
            max_length=UNIT_MAX,
            description="The item's unit the page showed `quantity` in, from "
            "the catalogue. A line of an item counted in another unit since is "
            "refused (unit_changed), never recorded in the new unit.",
        ),
    ]

    @field_validator("package_size")
    @classmethod
    def _size(cls, size: Decimal | None, info: ValidationInfo) -> Decimal | None:
        """Refused on `package_size` itself, in words naming both fields, for
        whoever writes a client. `info.data` holds the fields declared before
        this one that were not refused: so `package_id` stays before it, and
        one refused already is left at that."""
        if "package_id" not in info.data:
            return size
        if info.data["package_id"] is not None and size is None:
            raise PydanticCustomError(
                "package_size_required",
                "package_size is required when package_id is given: the "
                "size, in the item's unit, the package was counted in, from "
                "the catalogue",
            )
        if info.data["package_id"] is None and size is not None:
            raise PydanticCustomError(
                "package_size_without_package",
                "package_size is only allowed with package_id",
            )
        return size


class LengthReport(BaseModel):
    """What a volunteer says is really on an open package, for an
    administrator to check (docs/decisions/0020, 2026-11-12)."""

    open_package_key: uuid.UUID
    left: Remaining
    unit: Annotated[
        str,
        Field(
            min_length=1,
            max_length=UNIT_MAX,
            description="The item's unit the page showed `left` in, from the "
            "catalogue. A report on an open package of an item counted in "
            "another unit since is refused (unit_changed), as a line is, never "
            "kept in the new unit.",
        ),
    ]


class Batch(BaseModel):
    key: uuid.UUID
    kind: VolunteerKind
    volunteer_id: Number
    from_location_id: Number | None = None
    to_location_id: Number | None = None
    install_number: Number | None = None
    lines: Annotated[
        list[Line],
        Field(
            max_length=MAX_LINES,
            description="One line per item and package (or loose): a batch "
            "naming one on more than one line is refused, 422 with type repeated "
            "on each of those lines. None only in a report.",
        ),
    ]
    used_up: Annotated[
        list[uuid.UUID] | None,
        Field(
            max_length=MAX_LINES,
            description="Keys of open packages the volunteer says are used up, "
            "for an administrator to confirm (docs/decisions/0020): with a cut "
            "that took all they showed, or alone, in a report.",
        ),
    ] = None
    lengths: Annotated[
        list[LengthReport] | None,
        Field(
            max_length=MAX_LINES,
            description="What the volunteer says is really on open packages, "
            "by key, for an administrator to check (docs/decisions/0020): "
            "with a cut, or alone, in a report. Never an adjustment.",
        ),
    ] = None
    changed_from: Annotated[
        list[Line] | None,
        Field(
            max_length=MAX_LINES,
            description="The lines this batch held when its cart was put back "
            "under its key, before the volunteer changed it: what it holds "
            "less of than these, and than another batch recorded under the "
            "key, the volunteer took off, kept for an administrator when "
            "this batch is refused (409 key_reused). Compared with nothing "
            "else: a batch sent again with other lines here is the same batch.",
        ),
    ] = None

    @model_validator(mode="after")
    def _sides(self) -> Batch:
        if (self.kind == "report") != (not self.lines):
            raise ValueError("a report has no lines; every other batch has some")
        reports = bool(self.used_up or self.lengths)
        if self.kind == "report" and not reports:
            raise ValueError(
                "a report names the open packages used up, or what is on them"
            )
        if reports and self.kind not in ("move", "install", "report"):
            raise ValueError("only a batch taking from where it is reports")
        has_from, has_to = SIDES[self.kind]
        if (self.from_location_id is not None) != has_from or (
            self.to_location_id is not None
        ) != has_to:
            sides = " and ".join(
                side for side, wanted in (("from", has_from), ("to", has_to)) if wanted
            )
            raise ValueError(f"a {self.kind} has only a {sides} location")
        if self.kind == "open":
            if self.from_location_id != self.to_location_id:
                raise ValueError("an opening is at one place: from and to are it")
            if any(line.package_id is None for line in self.lines):
                raise ValueError("an opening's lines each name a package")
        elif self.from_location_id == self.to_location_id:
            raise ValueError("from and to must be different locations")
        for line in self.lines:
            taken = line.open_package_key is not None and self.kind != "open"
            if taken and (line.package_id is not None or self.kind == "receipt"):
                raise ValueError("a line is taken from an open package, or not")
            if line.whole and not (taken and self.kind == "move"):
                raise ValueError("only a move's open package moves whole")
        if self.install_number is not None and self.kind != "install":
            raise ValueError("only an install has an install number")
        return self


# Where in the batch, as pydantic gives it ("lines", 2, "item_id"), and what.
# The 422 sends the kind, never words: the scan page words each kind itself
# (docs/decisions/0022). No kind takes values yet.
ProblemKind = Literal[
    "not_found",
    "not_whole",
    "not_whole_packages",
    "one_package",
    "open_package_closed",
    "open_package_elsewhere",
    "open_package_whole",
    "moved_whole",
    "package_resized",
    "unit_changed",
    "refused",
    "retired",
    "retired_package",
    "retired_package_none_left",
    "repeated",
]
Problem = tuple[tuple[str | int, ...], ProblemKind]
# A line's package_size refused by Line's own check, in FastAPI's own 422.
SizeProblemKind = Literal["package_size_required", "package_size_without_package"]


class BatchProblem(BaseModel):
    """One problem of a refused batch, shaped as FastAPI's own are: where,
    and its kind, which the scan page words itself."""

    type: ProblemKind
    loc: list[str | int]


class BatchRefusedReply(BaseModel):
    detail: list[BatchProblem]


class BodyProblem(BaseModel):
    """One problem of a body that cannot be a batch at all, in FastAPI's
    own 422: pydantic's type, where, and words for whoever writes a
    client, which the scan page does not show."""

    type: str
    loc: list[str | int]
    msg: str


class SizeProblem(BaseModel):
    """A line's package_size refused by its own check, in FastAPI's own
    422: one of the kinds there the scan page words itself."""

    type: SizeProblemKind
    loc: list[str | int]
    msg: str


class BodyRefusedReply(BaseModel):
    detail: list[SizeProblem | BodyProblem]


class RecordedLine(BaseModel):
    """A line of the transaction recorded under a batch's key, as a batch
    sends one (Line), so the scan page compares it with what it sent."""

    item_id: int
    quantity: Decimal
    package_id: int | None
    # The package's size now, with package_id.
    package_size: Decimal | None
    open_package_key: uuid.UUID | None
    whole: bool | None
    # The unit it was recorded in.
    unit: str


class RecordedLength(BaseModel):
    """A report of what is on an open package, as recorded (LengthReport)."""

    open_package_key: uuid.UUID
    left: Decimal
    # The unit it was reported in.
    unit: str


class RecordedBatch(BaseModel):
    """The transaction recorded under a batch's key, as a batch sends one
    (Batch), with its id."""

    transaction_id: int
    kind: VolunteerKind
    volunteer_id: int
    from_location_id: int | None
    to_location_id: int | None
    install_number: int | None
    lines: list[RecordedLine]
    used_up: list[uuid.UUID]
    lengths: list[RecordedLength]


class Saved(BaseModel):
    transaction_id: int


class KeyReusedReply(BaseModel):
    reason: Literal["key_reused"]
    recorded: Annotated[
        RecordedBatch | None,
        Field(
            description="The transaction recorded under the key, as a batch "
            "sends one, for the page to show what differs; none if no "
            "volunteer's batch recorded it. Only one sending the key learns "
            "of it."
        ),
    ] = None
