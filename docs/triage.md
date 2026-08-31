# Triaging an issue somebody else filed

`scripts/untriaged.py` finds the beads that arrived from GitHub and prints the
commands that finish them. This is the part it cannot do: deciding what the
thing actually is.

It is written down because the mechanics are the easy half. Renaming a bead
takes a second; deciding that a one-line complaint is really a known bug in the
finder, belongs to nobody's epic, and should be closed as a duplicate takes
judgement, and judgement that lives in one person's head is not a process.

---

## Before anything else: read it as a report, not a request

Somebody outside this project wrote it. They described a **symptom**, in their
words, from where they were standing. They did not know which component it
touches, whether it is already tracked, or what this project calls it.

So the first question is never *"what should we do about this?"* but **"what is
actually being reported?"** — and often the honest answer is that it is not yet
clear, in which case the triage is to ask them, on the GitHub issue, before
touching the bead.

An arrival is the only kind of bead in this tracker written by somebody who was
not thinking about the tracker. Treat the text as evidence rather than as a
specification.

---

## The five decisions

### 1. Is it already tracked?

Run the searches the helper offers. They are the longest, least common words
from the title, which is what you would have picked by eye.

If it duplicates something, say so on the GitHub issue with a link to what
covers it, then close the bead — `bd close <id>` — rather than deleting it. The
`external_ref` is the only record that the issue was ever answered, and closing
keeps it.

Two things that look like duplicates and are not:

- **A second report of the same symptom with a different cause.** Two people
  saying "search is slow" may be describing an index and a network. Keep both
  until one is understood.
- **Something already fixed but not released.** That is not a duplicate, it is a
  question about when. Answer it on the issue and close the bead.

### 2. What kind of work is it?

The vocabulary this tracker actually uses, in the order it uses it:

| Type | What it means here |
| --- | --- |
| `task` | The default, and most of the tracker. Work that has to be done and is not a defect |
| `bug` | Something behaves other than as this project says it does. Not "I wish it did more" |
| `feature` | A capability that does not exist yet |
| `chore` | Maintenance nobody outside would notice — a dependency, a rename, a test |
| `epic` | Holds other issues. Never work in itself |
| `decision` | Something a person must decide before code is worth writing |

**`bug` is the one most often wrong.** An arrival is usually phrased as a
complaint, and a complaint about behaviour that was designed on purpose is a
`feature` request or a `decision`, not a defect. If answering it would need
somebody to change their mind rather than fix something, it is not a `bug`.

### 3. How urgent?

| | |
| --- | --- |
| **P0** | Something is broken now and somebody is stuck. Rare, and never assigned to be polite |
| **P1** | Blocks a goal that is currently being worked on |
| **P2** | Real work, no deadline. The commonest and the right default when unsure |
| **P3** | Worth doing, nobody is waiting |
| **P4** | Filed so it is not forgotten, and that is all |

An arrival from outside has no special claim on urgency. Somebody taking the
trouble to file an issue is not evidence that it blocks anything — and quietly
inflating it to be welcoming is how a priority scale stops meaning anything.
Answer them warmly on the issue instead; that is where the thanks belong.

### 4. Whose work does it join?

Give it a parent if it clearly belongs to an epic that is open. If it does not
belong to one, leave it parentless — an epic is not a folder, and forcing an
arrival into the nearest one makes that epic impossible to finish.

If two or three arrivals turn out to be the same underlying thing, that is when
an epic is worth making, and not before.

### 5. What should it be called?

Beads in this tracker are named as **claims**, not labels — *"A typo in one word
of a volunteer's name scores below the threshold"*, not *"Search bug"*. The name
should say what is wrong or what should be true, so that a commit message
carrying it reads as a sentence.

Rename to that, keeping the reporter's meaning rather than their words:

```bash
bd rename <arrival id> inventory-tng-<short-name>
```

---

## Rewriting the body, and how far to go

The beads in this tracker are arguments: what was measured, what was read rather
than assumed, what was considered and rejected. An arrival is one or two
sentences from somebody who owed us none of that.

**Do not fabricate the rest.** The temptation is to write the bead you wish you
had received, and the result is a description whose confidence is not backed by
anything — the worst kind, because the next person believes it.

What to do instead:

- Keep what they wrote, marked as what they said.
- Add what you checked yourself, marked as that, with the date.
- Say plainly what is still unknown.

A three-line bead saying "reported X; confirmed X happens on Y; cause unknown"
is worth more than thirty lines of inference.

---

## What must never go into the bead

The tracker is public and its history cannot be recalled —
[0029](decisions/0029-the-issue-tracker-is-public.md). An arrival is the likeliest
place for that to go wrong, because somebody outside the project wrote it
without knowing where it would end up.

So before pasting anything from an issue into a bead, check it for the four
things 0029 names — personal data, credentials, an exploitable detail with no
mitigation, and anything given in confidence. If the GitHub issue itself
contains one of them, that is a separate problem and the issue needs attention
before the bead does.

---

## When to close instead

Not everything filed is work. Closing an arrival with a reason on the GitHub
issue is a complete and respectful outcome:

- It duplicates something tracked — link it.
- It describes behaviour that is deliberate — link the decision record that
  argues it, so the answer is the project's rather than yours.
- It is a question rather than a request — answer it.
- It asks for something this project has decided not to do — say so, and say
  where that was decided.

What is not a reason: that it is small, that it is inconvenient, or that nobody
has time. Those produce a bead at P3 or P4, not a closure.
