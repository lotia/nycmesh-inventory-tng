# 0004 — Who signs in

**Status:** accepted · 2026-10-30 · amended by 0012 (TOTP may be
optional; sign-in in detail)

## Context

Volunteers record stock moving while they work, often on a phone, in a
storage space or on a roof. Making them sign in would slow that down and keep
some from recording at all. Administrators change the catalogue, locations
and volunteers, so their access needs real protection.

## Decision

- **Volunteers do not sign in.** They pick their name and append to the
  ledger. Each transaction is attributed to that name.
- **Administrators sign in with a password and TOTP.** Passwords are hashed
  with argon2-cffi, and TOTP uses pyotp. Sessions are kept on the server.
- **TOTP is required by default.** One setting switches it off for
  development. The development docs will name it.
- **Passkeys and OIDC come later.**
- **A provider's identity never grants authority.** Signing in through a
  provider proves who someone is, not what they may do. Accounts are never
  linked automatically by email.
- **No home-made cryptography.** This relies on the rule in AGENTS.md: only
  established libraries, arranged by thin glue.

We rejected sign-in for volunteers too: the ledger can absorb a wrong name,
but it cannot record what nobody entered.

## Consequences

- Anyone who can reach the volunteer pages can append under any name. Because
  the ledger is append-only and counts catch errors (see 0003), a mistake is
  visible and can be corrected.
- Only administrator actions are protected by sign-in.
- Sign-in is this project's own glue over established libraries, so that glue
  needs careful review and tests.
- Adding passkeys or OIDC later must not change what grants authority.
