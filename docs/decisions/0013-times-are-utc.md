# 0013 — Times are UTC; shown in the site's zone, or a ledger event's own

**Status:** accepted · 2026-11-02 · amended 2026-11-08 by 0016 (who checks
the zone), 2026-11-10 (a zone per location, kept by each ledger event)

## Context

NYC Mesh works in New York, where clocks move an hour twice a year. A time
kept in local time is ambiguous for the hour the clocks go back, and two
times either side of a change do not subtract correctly. Python, PostgreSQL
and browsers each have a zone of their own, which can differ from the
site's.

## Decision

- **Every time is stored, compared and sent in UTC.** Columns are
  `timestamptz`, Python uses `datetime.now(UTC)`, the scan page uses epoch
  milliseconds, and every database connection sets `TimeZone=UTC`, so
  times come back in UTC whatever the server is set to.
- **One site time zone shows them**: `site_settings.time_zone`, an IANA name,
  `America/New_York` by default. Administrators change it, and the change is
  recorded in the history. The app refuses a zone zoneinfo does not know.
- **A location may have a zone of its own** (inventory-tng-dp5), set on its
  page; without one it takes its parent's, and at the top the site's.
- **Each ledger transaction keeps the zone it happened in**, beside its UTC
  time: the zone of the place stock left, or arrived at for a receipt (a
  count or adjustment has one place), as that place had it then. A zone
  set later does not change the clock an event happened at. Pages show a
  ledger event in its own zone, with the abbreviation (13:05 CST), so it
  reads as the clock on the wall there. A move out of a volunteer's
  custody, a person rather than a place, keeps the zone where it arrived.
  Times not tied to a place
  (sign-in, codes, the activity feed) stay in the site's. We rejected
  keeping both a move's zones (more to show, for little) and showing every
  event in the site's zone (it hides the local clock a hub kept).
- **Only what people read is converted**, with `local_time.show()`, which
  uses zoneinfo's rules for the zone, so it shows EST or EDT as the date
  requires. The tzdata package ships those rules, so they do not depend on
  the server's operating system.

We rejected storing local times (ambiguous once a year) and each browser's
own zone (a volunteer travelling, or a laptop set wrong, would see times
that disagree with everyone else's).

## Consequences

- Times not tied to a place are shown in one zone for everyone, wherever
  they are; a ledger event, in the zone it happened in.
- Changing the site's zone changes how those times are shown, never what
  is stored; changing a location's zone changes only events written after.
- New code shows a time through `local_time.show()`, never by formatting a
  stored time directly.
