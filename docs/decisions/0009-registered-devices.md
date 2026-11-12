# 0009 — Devices register with a code, and the ledger records them

**Status:** accepted · 2026-11-01 · amends 0003 (an unregistered device is
blocked) · amended 2026-11-10 (revoking ticked devices at once)

## Context

Volunteers do not sign in (0004), but we must be able to follow who checked
something in or out. A name can be anything someone types, so it cannot be
the only thing a record holds. The old project told devices apart with a
token in `localStorage`, sent as a header by its single-page app. A
server-rendered app cannot see `localStorage` on ordinary page loads or form
posts, and Safari can clear script-written storage after seven days without a
visit. The old project's device was attribution only; here we also want to
decide which devices may record anything at all.

## Decision

- **A device registers once, with a code.** An administrator makes a code
  with an expiry and a use limit, and hands it out as a QR code in the room or
  as a phrase of five random words for a remote volunteer: one code, two
  spellings. The code is checked first; only then does the device register,
  with a random id of its own. The code is never the device's identity.
- **The device is a cookie the server sets**: `HttpOnly`, `Secure`,
  `SameSite=Lax`, 400 days. Every request that needs a device reads the
  cookie and looks up its row, so revoking a device refuses its next request.
  Only SHA-256 hashes of cookies and phrases are stored. Nothing is signed:
  the row is what decides, and a made-up value finds none.
- **An unregistered device is blocked**, amending 0003's "a volunteer is
  never blocked". No cookie, or one without a current row, means registering
  first. A cleared cookie means registering again.
- **Revoking a code stops new registrations only.** Its devices stay
  registered. Each device records the code it used, when and from where, and
  an administrator reviews a code's devices by how long before the revocation
  they registered, revoking suspicious ones one at a time, or, after a
  compromise, lists every device registered since it began and revokes
  those ticked at once, after saying how many (inventory-tng-ph9). Nothing
  revokes every device unasked, which would stop honest volunteers
  mid-shift.
- **Each ledger transaction records its device** beside the volunteer picked
  (inventory-tng-8i4.11). A device id therefore comes close to identifying
  whose phone it is: it may be logged, as any id, and is never a metric
  attribute.
- **No work is lost to registering.** The scan page keeps its cart and
  outbox in `localStorage`, which does not depend on the cookie. A save
  refused for want of a device stays in the outbox and is resent, with the
  same idempotency key, once the device registers.

We rejected `localStorage` for the device itself (invisible to the server
without script, and cleared by Safari), signed tokens (the row lookup makes
the signature redundant, and keys would need rotating), and browser
fingerprinting (unreliable, and hostile to privacy).

## Consequences

- Volunteers register each browser they use, once, and again after clearing
  site data or in a private window. A QR code opens the phone's default
  browser, which may not be the one holding the scan page's outbox; the phrase
  avoids that.
- A leaked code registers devices until it expires, is used up or is revoked.
  Short expiries and small use limits keep that window small.
- The cookie can be copied off a phone. Something stronger, such as passkeys
  or device-bound cookies, is researched in inventory-tng-8i4.43.
- Guessing a phrase is limited to ten wrong attempts a minute per address,
  shared by every instance (0012, Amendment): each counts before it is
  checked and a good one is taken back, so a room of phones can register.
  At about 64 bits per phrase that is far more than enough.
- A registered device sees every current volunteer's name, and nothing else
  of them (`catalogue.py`, `VolunteerEntry`), and the names of their custody
  locations, made in their names (`locations.add_custody`). An unregistered
  device is refused the catalogue, so sees none of it.
