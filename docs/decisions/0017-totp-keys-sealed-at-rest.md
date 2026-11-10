# 0017 — Authenticators' TOTP keys are sealed at rest, with keys outside the database

**Status:** accepted · 2026-11-09 · amends 0012 (administrator sign-in)

## Context

A TOTP key is a shared secret: whoever holds it makes the same codes as the
administrator's phone. They were stored as they were, so a dump or a backup
of the database could make codes for every administrator, who would then be
protected by their password alone (inventory-tng-8i4.48). NIST SP 800-63B-4
asks only for access control; OWASP's Cryptographic Storage cheat sheet
recommends encrypting in the application with the key kept apart, and
backups are where a copy is likeliest to leak.

## Decision

- **Each key is sealed with AES-GCM** from the cryptography package, through
  a thin wrapper (`totp_keys.py`) that calls only its public API (AGENTS.md
  rule 3), with a random 96-bit nonce per seal.
- **Bound to its administrator.** The administrator's id is AES-GCM's
  associated data, so a sealed key copied into another administrator's row
  does not open: someone who can write to the database cannot plant a key
  they know on another account. The authenticator's own id is not used: the
  database gives it on insert, after the key is sealed, and moving a key
  between one administrator's own authenticators gains nothing.
- **Keys live in `TOTP_KEYS_DIR`**, one file per version, as a Kubernetes
  Secret mounted into the app's pods only. The version sealed with is
  stored beside each key, so keys rotate: the newest seals, any present
  opens, and `reseal` moves every authenticator to the newest.
- **A missing version locks no one out.** An authenticator that cannot be
  read takes no code and is logged; its administrator signs in with a
  recovery code or a reset link and enrols again.

We rejected encrypting in PostgreSQL (pgcrypto), which would put the key in
queries and logs on the database server and is logic in the database
(0016), and a key in a plain environment variable, which is easier to leak
through a process listing or a crash report than a mounted file.

## Consequences

- A database copy without the Secret cannot make codes; the Secret is backed
  up apart from it (DEPLOYMENT.md, TOTP keys).
- Every deployment needs `TOTP_KEYS_DIR`; development uses
  `.local/totp-keys`, which `mise run setup` fills.
