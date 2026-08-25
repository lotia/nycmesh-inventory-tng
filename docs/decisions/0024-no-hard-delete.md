# 0024 — No hard-delete path is built, and none will be

**Status:** accepted

## Context

Asked by the project owner once every reference to an item had become `PROTECT`
and the admin had stopped offering a Delete on one: is there still an "only if
you really know what you are doing" path, hard to reach but there? The proposal
was a management command, defaulting to a dry run and printing what it was
about to destroy, with the hoop being that it takes a shell on the server.

Three situations get confused with one another, and only the third asks for
anything new.

**A row created a minute ago by mistake, with nothing recorded about it.** It
loses nothing either way, which is exactly why it does not need a mechanism of
its own. `active` takes it out of every list, and where the mistake is the
row's *content* rather than its existence — a name typed wrong, the wrong
category — the repair is to correct it on the form, which is also what frees
the name for its proper use, `Item.name` being unique.

**A row that should stop appearing.** That is
[retirement](0019-retired-means-not-offered.md), it is built, and the
[administrator's guide](../../guides/administrator.md) tells an administrator to
use it.

**A row that must genuinely leave the database** — a corrupted import, an
erasure somebody is legally owed. Rare, irreversible, and destructive of
history the [ledger](0008-stock-ledger-transfer-graph.md) exists to keep. This
is the only case, and four things about it settle what to do.

**Where such a delete would matter most, the database refuses it, and no
command of ours may overrule that.** The ledger's two tables carry triggers that
raise on any `UPDATE`, `DELETE` or `TRUNCATE`
([0016](0016-invariants-for-every-writer.md) is why they are triggers). An item
that has moved cannot go without its movements, and a command that tried would
be refused by Postgres rather than by Django —
`backend/src/inventory/tests/test_ledger.py` holds that refusal from four
directions. So the case that argues loudest for the mechanism is the case it
could not serve.

**Where it would work, the hoop already exists, and it is the hoop that was
proposed.** `PROTECT` is enforced in Django's collector and was never asked of
Postgres, so a statement issued through `manage.py dbshell` reaches the row
already; [the data model](../data-model.md#label) says as much where it says
what `PROTECT` buys. "Having a shell on the server" is therefore not a hoop the
command would add. It is the hoop that is already there, and the command would
be a way of making it lower.

**What a command would genuinely add is the count it prints first.** That is
worth something, and it is not worth much: the change form already lists an
item's identifiers, the label list already answers which stickers name it, and
an attempted delete already names the first reference standing in the way.
Against that, a destructive command nobody should run is a destructive command
nobody has run, so on the day it is needed it will not have been exercised for
years, by anybody, in any state of the schema.

**And an erasure request is not about these rows.** An item and a location
carry no personal data. A volunteer carries a name, an email and a Slack id, and
so does every historical copy of that volunteer that `django-simple-history`
keeps. A command that hard-deleted an item would do nothing for such a request,
and one that could serve it is different work against a different model.

## Decision

**No hard-delete mechanism is built, and none will be.** Nothing is added by
this record; what it settles is that the gap is deliberate, so that the question
is not asked again from the shape of the code.

**That is narrower than "nothing here deletes anything", and the difference
matters.** Django's admin still offers a Delete on a volunteer, a category, a
vendor, an offer, an identifier, a label and an unlabelled location, and for a
row nothing refers to it goes through. Each of those is a question about one
screen and is argued where it belongs —
`inventory-tng-ls6d` for a label, `inventory-tng-k50y` for an identifier, and
`inventory-tng-k2sg` closed the item's. This record is not a blanket over them
and must not be cited as one. What it decides is that the answer to any of them
is never "and here is the supported way to do it properly".

**Retirement is the answer the application gives**, and a correction to the
ledger is a compensating movement rather than an edit. Both are built and both
are documented where the person doing them will be.

**Removing a row from the database is a database operation.** It is done with a
shell on the server by somebody who can read the schema and the triggers, and it
is deliberately made no easier than that. The ledger's own tables are not
reachable that way either without first dropping their triggers, which is a
different act, under a different name, that leaves a trace in the schema.

**`MAY_CASCADE` in `backend/src/inventory/tests/test_models.py` stays empty.** An
entry in it is an argument that losing something along with the row naming it is
acceptable, and the test refuses one without a reason beside it.

## Consequences

- **An item created by mistake cannot be removed through any interface this
  project offers**, which is a real loss of convenience and the price of the
  paragraph above. What remains is to retire it or to correct it, and neither
  costs anything to somebody who has not yet recorded anything against it.
- **Rows accumulate.** A catalogue that only ever grows is the intended state,
  and the lists a volunteer sees are filtered by `active` rather than by what
  exists.
- **A legal-erasure requirement is still unanswered**, and the Context above
  says why it would not be answered from here anyway. Somebody has to open that
  question when it arrives; nothing in this repository will raise it.
- **Two of the buttons the Decision leaves standing are open questions**, and
  neither is decided here: `inventory-tng-ls6d` and `inventory-tng-k50y`.
  Whichever way they go, this record is unaffected.
