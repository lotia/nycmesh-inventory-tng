"""What this application measures about itself, in one list.

Every counter this code increments is declared here rather than beside the
call that increments it, and that is worth a sentence because the alternative
looks tidier. A counter is identified by its name: two modules calling
`create_counter("inventory.appends")` get two instruments, and a collector
shows two series that have to be added up by whoever is reading. Naming them
once makes that impossible, and it means the answer to "what does this
application measure" is a file rather than a search.

WHAT EARNS ONE, since not everything does. A counter is for a state change
somebody would page on or draw a graph of: work arriving, work refused, work
recorded. Anything whose value is in the detail -- which line of a batch was
wrong, which volunteer -- is a log record, because a metric cannot carry
detail without turning every distinct value into a series of its own.

THE ATTRIBUTES ARE A CLOSED SET, and `inventory_tng.redaction` is what closes
it: an attribute this application does not declare there never reaches an
exporter. So the values below are words chosen in this code -- `recorded`,
`rejected` -- and never anything a caller supplied.
"""

from typing import Any

from opentelemetry import metrics

# One meter for the application, named for the package. The instrumentation
# scope a collector shows is this name, which is how our own measurements are
# told apart from the ones Django's and psycopg's instrumentations produce.
METER = metrics.get_meter("inventory")


def counter(name: str, description: str) -> Any:
    """One counter, declared at import.

    Safe at import even though the SDK starts later: the API hands back a
    proxy instrument that begins recording the moment a provider is set, so
    there is no first-use dance to write. `inventory_tng.debugging` measured
    that rather than assuming it.
    """
    return METER.create_counter(name, description=description)


# The endpoint this project exists for. `outcome` is every way the append path
# can end -- see `StockTransactionCreateView` -- so a rise in `rejected` is
# visible without anybody reading a log, which is the question the epic asks
# this sweep to answer.
APPENDS = counter("inventory.appends", "Batches offered to the ledger, by what became of them.")

# What those batches actually moved. Separate from the count of batches because
# one request carries up to five hundred lines, so the two answer different
# questions: how often people are recording, and how much is moving.
MOVEMENTS = counter("inventory.movements", "Stock movements recorded, by the kind of transaction.")

# Additions to the pick-list, by outcome. A rise in conflicts is the duplicate
# spellings decision 0008 counts, happening again.
VOLUNTEERS = counter("inventory.volunteers", "Attempts to add somebody to the pick-list, by outcome.")

# Stickers: minted, printed onto a sheet, revoked. One counter with an
# `outcome` rather than three, so a dashboard is one query.
LABELS = counter("inventory.labels", "Label operations, by what was done.")

# Administrator edits to what is already recorded -- the operations decision
# 0012 reserves. `collection` says which of them.
CATALOGUE_EDITS = counter("inventory.catalogue_edits", "Edits to a catalogue row, by collection.")

# An unfinished sign-in, and a session too old to change something; `reason`
# names which. A 400 is deliberately not counted here -- that is the request
# being wrong rather than the caller, and it is on the record the endpoint
# writes.
REFUSALS = counter("inventory.refusals", "Requests refused on account of the caller, by reason.")

# Management commands, which run unattended and so are the one thing here
# nobody watches. `command` names it and `outcome` says whether it finished.
COMMAND_RUNS = counter("inventory.command_runs", "Management command runs, by command and outcome.")
