# 0013 — Times are UTC; one site time zone shows them

**Status:** accepted · 2026-11-02

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
  recorded in the history. PostgreSQL refuses a zone it does not know.
- **Only what people read is converted**, with `local_time.show()`, which
  uses zoneinfo's rules for the zone, so it shows EST or EDT as the date
  requires. The tzdata package ships those rules, so they do not depend on
  the server's operating system.

We rejected storing local times (ambiguous once a year) and each browser's
own zone (a volunteer travelling, or a laptop set wrong, would see times
that disagree with everyone else's).

## Consequences

- Times are shown in one zone for everyone, wherever they are.
- Changing the zone changes how every time is shown, never what is stored.
- New code shows a time through `local_time.show()`, never by formatting a
  stored time directly.
