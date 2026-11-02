# 0011 — Whose name a batch is saved under

**Status:** proposed · 2026-11-02 · to review with an inventory
administrator · builds on 0004 (volunteers pick their name) and 0009
(devices register)

## Context

Volunteers do not sign in; they pick their name from a list, and each batch
is recorded under it (0004). We want that name to be right, because it is how
we follow who took what, and we do not want saving to become a chore, or
volunteers will stop recording at all.

Most phones are a volunteer's own, used by one person for many batches in a
row. Some are shared, such as a tablet kept at a hub, and pass from hand to
hand. On a shared phone, a page that remembers the last name lets the next
person save under someone else's without noticing. A page that forgets it
makes everyone pick their name again for every batch.

## Decision

- **The page remembers the name**, with the kind of batch and its places, so
  a volunteer filling several batches in a row picks them once.
- **Save always says whose name it is**: "Saving as Ada", with a Change
  button beside it. Saving never asks for confirmation.
- **After a while unused, the page asks once**: "Are you Ada? This phone
  hasn't been used for a while", with "That's me" and "Not me". It asks when
  the page opens, or is shown again, after longer than the set time since the
  cart last changed. "Not me" clears the name and goes to the list. It does
  not stop anyone saving.
- **Administrators set the time**, in the database, not in the server's
  configuration: `scan_settings.name_check_minutes`, 15 minutes to begin
  with, where 0 never asks, changed on the administrators' Settings page.
  Changes are recorded with who made them, like any catalogue change. The
  page reads it with the catalogue, which a page left open fetches again
  every ten minutes, so a change reaches even a hub tablet that is
  never reloaded.

## Options weighed

Each with what happens to Ada, who saves three batches in ten minutes on her
own phone, and to Ben, who picks up the hub tablet an hour after Ada.

| Option | Ada | Ben | Verdict |
| --- | --- | --- | --- |
| Remember the name, never ask | Picks once | Saves as Ada unless he notices | Wrong names on shared phones |
| Forget it after every Save | Picks three times | Must pick his own | Annoying on every phone |
| Confirm the name at every Save | Taps three extra times | Asked, and likely taps through | Annoying, and the habit makes it useless |
| Forget it after a while unused | Picks once | Must pick his own | Silent: Ada wonders where her name went |
| Remember, show it at Save, ask after a while unused (chosen) | Picks once, never asked | Asked once, picks his own | Accurate where it matters, quiet otherwise |

## Questions for an administrator

- Is 15 minutes right? Shorter catches more hand-overs and asks more often;
  longer asks less and misses more.
- Should the question stop saving until it is answered? Today it does not, so
  a volunteer in a hurry can ignore it.
- Do shared phones need something more, such as a shorter time for hub
  devices only? Devices are registered (0009), so a per-device setting is
  possible later.

## Consequences

- A volunteer who ignores both "Saving as" and the question can still save
  under the wrong name. The ledger records the device as well (0009), so a
  wrong name can be traced and corrected with an adjustment.
- The setting is one row, `scan_settings`, which the scan page's other
  settings can join later.
