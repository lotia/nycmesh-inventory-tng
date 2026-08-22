#!/usr/bin/env bash
# From a fresh clone to a database with something in it, in one command.
#
# Every line below is a command DEVELOPERS.md already describes; what this adds
# is the order and the fact that you do not have to type them. It invents no
# configuration of its own, and where a step cannot be automated it says so
# where you have to do it rather than leaving you to find out.
#
# Safe to run again: it writes nothing it has already written, and it will not
# touch a .env that exists.
#
# Usage: scripts/bootstrap-dev.sh

set -euo pipefail

ROOT=$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)
cd "$ROOT"

step() { printf '\n== %s\n' "$1"; }
note() { printf '   %s\n' "$1"; }
die() {
  printf '\n%s\n' "$1" >&2
  exit 1
}

# ---------------------------------------------------------------------------
# The two things this cannot install for you.
# ---------------------------------------------------------------------------

command -v mise >/dev/null 2>&1 ||
  die "mise is not installed, and it is what installs everything else.
Install it with:  curl https://mise.run | sh
Then open a new shell and run this again."

if command -v docker >/dev/null 2>&1; then
  compose=(docker compose)
elif command -v podman >/dev/null 2>&1; then
  compose=(podman compose)
else
  die "Neither docker nor podman is installed, and PostgreSQL runs in one of them.
Either will do; rootless podman needs no daemon and no group membership."
fi

step "Toolchain"
mise trust
mise install

# uv arrives with the toolchain above. A shell with mise activated has it on
# PATH already; one without has to ask for it by name.
uv=(uv)
command -v uv >/dev/null 2>&1 || uv=(mise exec -- uv)

step "Configuration"
if [[ -e .env ]]; then
  note "You already have a .env. Left exactly as it was."
else
  # The guard above is what keeps this from overwriting anything; -f is here
  # only so a copy can never stop and ask.
  cp -f .env.sample .env
  note "Wrote .env from .env.sample. Nothing in it needs changing to start."
fi

step "PostgreSQL"
"${compose[@]}" up -d --wait postgres

step "Schema"
(cd backend && "${uv[@]}" run python src/manage.py migrate)

step "Something to look at"
(cd backend && "${uv[@]}" run python src/manage.py seed_demo_data)

cat <<'DONE'

== Over to you

The database is up, its schema is current, and there is a catalogue, a
warehouse, two volunteers and some stock in it. Three things are left, and
none of them is a step that was forgotten -- each needs a person.

1. Make yourself an account. It asks for a name and a password at the
   terminal, so it cannot be run for you:

     cd backend && uv run python src/manage.py createsuperuser

   You need one even to look at the volunteer's half today, which
   DEVELOPERS.md "Signing in" explains and is not how it is meant to end up.

2. Find an authenticator app before you sign in -- a phone app, or any TOTP
   tool. Your first sign-in stops and makes you enrol one, with no way past
   it and no setting that turns it off:
   docs/decisions/0013-administrator-sign-in.md is why. This is the wall
   people hit when nobody told them.

3. Start the two servers, in two terminals of your own:

     cd backend  && uv run python src/manage.py runserver
     cd frontend && npm install && npm run dev

   Then sign in at http://localhost:5173/accounts/login/.

DONE
