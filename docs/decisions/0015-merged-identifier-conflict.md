# 0015 — A taken identifier that nobody can see is a 409 naming who holds it

**Status:** accepted

## Context

`POST /api/volunteers` exists to serve one moment: somebody searched the
pick-list, was offered nobody who matched, and is adding themselves. The search
runs over `Volunteer.objects.selectable()`, which excludes merged duplicates and
retired records — the reason is on the manager, and
[decision 0008](0008-stock-ledger-transfer-graph.md) point 5 is why the list
exists at all.

Uniqueness was not scoped the same way. `email` and `slack_id` are unique where
present (see [docs/data-model.md](../data-model.md)), and DRF derives a
validator over *every* row from those partial indexes. The two halves therefore
disagree about exactly one population: the volunteers the list refuses to show.

That disagreement is a dead end. Sean's duplicate record is merged into Sean
McGinnis and keeps `sean@example.org`. A new Sean searches, is offered nothing,
self-registers with his own address, and is told "volunteer with this email
already exists" about a record the API will not show him, on the one screen
where there is nothing else to do. He has no way forward but to invent a second
address or a second spelling of his name — which is how the sheet this project
replaces came to hold 102 spellings for 65 people.

Three ways out were considered.

- **Show merged records in the search.** Cheap, and wrong in the other
  direction: the list is what a volunteer picks an actor from, and everything in
  it must be recordable against. Offering a record the batch endpoint then
  refuses would move the dead end rather than close it.
- **Scope the uniqueness check to selectable rows.** Also cheap. Two live rows
  would then share an address, which the partial index permits, and the next
  merge would have to decide which of them is real. It fixes the error by
  creating the duplicate the endpoint exists to prevent.
- **Answer with the record that holds it.** The most work, and the only one that
  leaves the volunteer somewhere to go.

## Decision

**When a self-registration is rejected solely because a merged or retired record
holds the identifier, answer 409 with a body naming the volunteer to act on.**

1. **The merge is followed forward.** A merge points the duplicate at the
   survivor and changes nothing else, so the record worth offering is the end of
   that chain, not the row that literally holds the address. Chains occur, and
   a cycle terminates the walk rather than hanging the request. Nothing can
   build a cycle any more — a trigger now refuses a merge into a record that
   has itself been merged
   ([0016](0016-invariants-for-every-writer.md)) — but the walk stays bounded:
   a database written to before that trigger existed can still hold one.
2. **A retired record names itself.** Nothing survived it, so the useful answer
   is that this record exists and an administrator has to restore it. Silently
   reactivating it is not an option: retiring a volunteer is an administrator's
   act, and [decision 0012](0012-two-populations.md) point 2 keeps undoing one
   an administrator's act too.
3. **A clash with a live volunteer stays a plain 400.** The searcher could have
   found them, so there is nothing to point at that the list does not already
   offer — and naming a volunteer who *is* findable would turn the endpoint into
   an address lookup for anyone who can guess one.
4. **The body is machine-readable.** `code` is `volunteer_merged` or
   `volunteer_inactive`, `field` is the identifier that clashed, `volunteer` is
   the record to act on serialised exactly as the pick-list serialises it, and
   `selectable` says whether it can be picked as it stands. A client branches on
   those and renders its own sentence; `detail` is there for one that does not.
   This follows the shape already set by the throttled response, where the
   number a client needs is a field rather than a phrase in the prose.
5. **409, not 400.** The submission is well formed and the conflict is with
   stored state the client could not have seen, which is what separates it from
   the field errors a volunteer fixes by retyping. It is also the status this
   API already uses for a batch that disagrees with recorded state.

The clash has to be the *only* complaint. A submission that is also missing a
name has not reached the dead end yet, and answering it with a conflict would
hide the field the volunteer still has to fix.

## Consequences

- The rejection a volunteer is most likely to hit now ends in "continue as Sean
  McGinnis" instead of a sentence about a record they cannot see.
- Two response shapes exist for one endpoint's rejections. A client that ignores
  the 409 falls back to showing `detail`, which is no worse than today.
- The 409 discloses one volunteer's display name and identifiers to whoever
  submitted a matching address — but only to somebody who already knew the
  address. A live holder is never disclosed, so the surface is limited to
  records the pick-list has retired. Worth weighing again when
  [decision 0012](0012-two-populations.md) point 3 is implemented and this
  endpoint stops asking for a session: it requires one today, so the disclosure
  is currently to somebody who has signed in.
- What a client is *told* is this endpoint's alone; which merges are possible at
  all is now the database's, and the admin is held to the same rule
  ([0016](0016-invariants-for-every-writer.md)).
