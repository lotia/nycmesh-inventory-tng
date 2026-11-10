# 0018 — The operator acts through commands run with `kubectl exec`

**Status:** accepted · 2026-11-10 · amends 0012 (administrator sign-in)

## Context

Administrators work only in the browser. Some things cannot be done there:
when no administrator can sign in, when every administrator's account may
be compromised at once, or when the key that seals authenticators is lost.
0012 gave the operator one way in without a command, `RECOVERY_TOKEN`, and
rejected a server command because on Kubernetes that means `kubectl exec`
into a pod, which GitOps setups often forbid, or a Job whose logs would
keep a printed link. Since then three operator commands have come to need
`kubectl exec` anyway: making a registration code and changing the scan
page's settings when no administrator can, and resealing authenticators
after a key rotation. NYC Mesh's clusters allow `kubectl exec`
(inventory-tng-8i4.50.8).

## Decision

- **The operator acts through commands run in the app's container**, on
  Kubernetes with `kubectl exec deploy/inventory -- python -m
  inventory_tng.<command>`. Each runs through `command.run_command`: one
  transaction, the app's own database login and settings.
- **A secret a command makes is printed to the operator's terminal**,
  never logged. Output of `kubectl exec` goes to the terminal that ran it,
  not to the container's logs.
- **No secret goes in a command's arguments**: the cluster's audit log
  records the command line.
- **Some things require `kubectl exec`**, and DEPLOYMENT.md says which.
  `RECOVERY_TOKEN` stays for letting one administrator back in without it.
- **Break-glass is two commands** (inventory-tng-8i4.50.2): `lockdown`
  retires every current administrator, as retiring one does, and revokes
  every registration code; `let-in` brings one back, retired or not, with
  everything they signed in with removed, and prints a reset link made by
  nobody (`made_by_id` NULL), which lasts an hour; `invite` makes an
  invite link the same way. A link's kind says what it does, and its maker
  who made it, so the pages and mails need nothing new for these.
  `lockdown` asks for how many administrators are current, so a slip
  refuses rather than surprises. Both are told to every administrator
  (0012) and kept in the history as `system:operator`.
- **A lost or leaked TOTP key retires authenticators in bulk**, the
  unreadable ones or all, keeping recovery codes. Sign-in asks for a code
  while any recovery code is unused, even with no authenticator left, so
  the password alone never gets in and lets its holder set up their own.

We rejected a Kubernetes Job for these, whose logs are kept and often
shipped elsewhere, so a printed link would be stored; a Job that mails the
link instead is the way out if a cluster that forbids exec must ever be
supported. We also rejected doing them from the browser, which a
compromised administrator could use to fight back.

## Consequences

- A cluster that forbids `kubectl exec` cannot run these commands.
- A command's log lines go to the operator's terminal, not the app's logs;
  what it changed is in the history.
- Whoever may exec into the app's pods can do anything the app can. They
  can already read its Secrets, `DATABASE_URL` among them, so this trusts
  nobody new.
