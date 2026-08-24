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
#
# Which of the two it was is remembered rather than thrown away, because the
# closing message below hands the reader commands to type in this same shell.
# A shell that could not find uv will not find npm either, and printing a bare
# `uv run ...` to somebody whose next keystroke answers `command not found` is
# the failure this script exists to spare them.
uv=(uv)
via=""
if ! command -v uv >/dev/null 2>&1; then
  uv=(mise exec -- uv)
  via="mise exec -- "
fi

step "Configuration"
if [[ -e .env ]]; then
  note "You already have a .env. Left exactly as it was."
else
  # The guard above is what keeps this from overwriting anything; -f is here
  # only so a copy can never stop and ask.
  cp -f .env.sample .env
  note "Wrote .env from .env.sample. Nothing in it needs changing to start."
fi

step "Git hooks"
# The checkers under scripts/ can refuse a commit before it exists, but only if
# git has been pointed at the directory holding them -- and that pointer lives
# in .git/config, which a clone does not carry. So a checkout arrives with the
# hooks in the tree and runs none of them until this runs.
#
# .beads/hooks is where they go because beads already owns that directory and
# already keeps five hooks of its own in it; DEVELOPERS.md#checking-it says why
# a second directory is not an option, and says that those five start running
# too, because this pointer is what arms all six.
HOOKS=.beads/hooks

# Asked about THIS directory rather than "am I inside a repository", which is a
# different question with the same answer nearly always. A copy unpacked under
# somebody else's checkout is inside one, and wiring it there would aim their
# git at a directory that is not in their tree -- turning every hook they had
# off, in a repository this script was never pointed at.
if ! [[ "$(git rev-parse --show-toplevel 2>/dev/null)" -ef "$ROOT" ]]; then
  note "This is not a git checkout of its own, so there are no hooks to install."
else
  # Made now so that every path below resolves to something whether or not this
  # clone has ever run beads. It writes nothing if the directory is there.
  mkdir -p "$HOOKS"

  # The link and its target first, and the pointer to them afterwards. A
  # checkout that cannot hold a symlink -- core.symlinks off, or a filesystem
  # without them -- is then refused while git is still running the hooks it was
  # running before, rather than aimed at a directory this script is about to
  # call broken.
  hook=$HOOKS/commit-msg
  if [[ -e "$hook" || -L "$hook" ]]; then
    note "The commit-msg hook is already there."
  else
    # Only reachable in a checkout made before the hook was tracked; a clone
    # taken since brings it along.
    ln -s ../../scripts/check-commit.sh "$hook"
    note "Linked $hook to the commit checker."
  fi

  # Said here rather than left for the first refused commit, because a hook
  # that cannot run is a hook git reports as a failed commit and nothing else.
  target=$(readlink -f "$hook" || true)
  [[ "$target" == "$ROOT/scripts/check-commit.sh" ]] ||
    die "$hook should lead to $ROOT/scripts/check-commit.sh and leads to ${target:-nothing}.
Delete it and run this script again to have it made afresh."
  [[ -x "$target" ]] ||
    die "$target is not executable, so git could not run it. Fix it with:

  chmod +x $target"

  # Every scope, not the local one alone, and the same question
  # scripts/check-setup.sh asks so the two cannot answer differently. A local
  # value overrides a global one, so reading only local means writing one over
  # somebody's global hooks -- signing, secret scanning, an employer's policy
  # -- and reporting that as success.
  #
  # Compared by where the paths lead rather than by how they are spelled: beads
  # writes an absolute one and this writes a relative one, and they name the
  # same directory. And one directory serves every worktree of a repository,
  # because core.hooksPath is shared with them all, so the main checkout's is
  # right for a linked worktree too and is what git will really run.
  main_hooks=$(dirname "$(readlink -f "$(git rev-parse --git-common-dir)")")/$HOOKS
  configured=$(git config --get core.hooksPath || true)
  if [[ -z "$configured" ]]; then
    # Whatever is in git's own hooks directory stops running the moment
    # core.hooksPath names anywhere else, and git says nothing about it. That
    # is where pre-commit, husky and a hand-written hook all put their files,
    # none of which set core.hooksPath -- so nothing above would have noticed.
    shopt -s nullglob
    for installed in "$(git rev-parse --git-path hooks)"/*; do
      [[ -x "$installed" && "$installed" != *.sample ]] || continue
      note "Note: ${installed##*/} and anything beside it in git's own hooks"
      note "directory stop running now, because one hooks directory is all git"
      note "takes. Move what you keep there into $HOOKS."
      break
    done
    shopt -u nullglob
    git config --local core.hooksPath "$HOOKS"
    note "Pointed core.hooksPath at $HOOKS. beads' five hooks run from here too."
  elif [[ "$(readlink -f "$configured")" == "$(readlink -f "$HOOKS")" ]] ||
    [[ "$configured" -ef "$main_hooks" ]]; then
    note "core.hooksPath already points at $HOOKS. Left as it was."
  else
    # Somebody chose this, and a single path is all git takes -- so overwriting
    # it would silently stop whatever they pointed it at. Theirs to move, not
    # ours.
    scope=$(git config --show-scope --get core.hooksPath 2>/dev/null | cut -f1)
    die "core.hooksPath is set to $configured${scope:+, in your $scope configuration}, and this repository's hooks are in $HOOKS.
Only one directory can be in force at a time, so this will not overwrite yours.
Move what you keep in $configured into $HOOKS, then run:

  git config --local core.hooksPath $HOOKS

and run this script again."
  fi
fi

step "PostgreSQL"
# Which port the container will be published on, and so which one has to be
# free. compose.yaml reads the same variable; .env is where a person sets it.
#
# Read unconditionally: the step above either found a .env or wrote one, so
# there is always a file here to read.
port=${POSTGRES_PORT:-5432}
while IFS= read -r line; do
  case "$line" in POSTGRES_PORT=*) port=${line#POSTGRES_PORT=} ;; esac
done < .env

# Asked before the attempt rather than after, because a second run of this
# script finds its own database already listening there and that is the
# ordinary case, not a clash. Only a bind that then fails makes it one.
# /dev/tcp is bash's own, so this adds no dependency.
taken=0
(exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null && taken=1

if ! "${compose[@]}" up -d --wait postgres; then
  if [[ "$taken" -eq 1 ]]; then
    die "PostgreSQL could not start, and something was already listening on port $port
before this run began -- another project's database, or one installed as a
service. Rather than stopping theirs, move ours, by putting BOTH of these in
.env with the same number in each:

  POSTGRES_PORT=5433
  DATABASE_URL=postgres://inventory:inventory@localhost:5433/inventory_tng

Both, because the first is the port the container is published on and the
second is where Django goes looking. Changing one alone aims Django at their
cluster instead. Then run this script again."
  fi
  die "PostgreSQL did not come up. The error above is the container tool's own."
fi

step "Schema"
(cd backend && "${uv[@]}" run python src/manage.py migrate)

step "Something to look at"
# Held rather than streamed, so that the one line worth acting on -- the label
# codes -- can be repeated under the closing message instead of being scrolled
# off the top of it.
seeded=$(cd backend && "${uv[@]}" run python src/manage.py seed_demo_data)
printf '%s\n' "$seeded"

codes=""
while IFS= read -r line; do
  case "$line" in "Labels to scan"*) codes=$line ;; esac
done <<< "$seeded"

cat <<DONE

== Over to you

The database is up, its schema is current, and there is a catalogue, a
warehouse, two volunteers and some stock in it. Three things are left, and
none of them is a step that was forgotten -- each needs a person.

1. Make yourself an account. It asks for a name and a password at the
   terminal, so it cannot be run for you:

     cd backend && ${via}uv run python src/manage.py createsuperuser

   You need one even to look at the volunteer's half today, which
   DEVELOPERS.md "Signing in" explains and is not how it is meant to end up.

2. Find an authenticator app before you sign in -- a phone app, or any TOTP
   tool. Your first sign-in stops and makes you enrol one, with no way past
   it and no setting that turns it off:
   docs/decisions/0013-administrator-sign-in.md is why. This is the wall
   people hit when nobody told them.

3. Start the two servers, in two terminals of your own:

     cd backend  && ${via}uv run python src/manage.py runserver
     cd frontend && ${via}npm install && ${via}npm run dev

   Then sign in at http://localhost:5173/accounts/login/.

DONE

if [[ -n "$via" ]]; then
  cat <<'ACTIVATE'
Those commands are prefixed because this shell cannot see mise's tools. That
is worth fixing once rather than typing forever: add the line the mise
installer printed to your shell's configuration and open a new terminal, and
then the prefix comes off every command in DEVELOPERS.md as well. Its
"Activate mise, then open a new shell" says exactly what to add.

ACTIVATE
fi

cat <<DONE
== What you should see, and what to try first

Signed in, the app at http://localhost:5173 draws a catalogue of six items,
four of them with a count beside them, and a pick-list of two volunteers. All
zeroes, or an empty list, means you are looking at a different database from
the one this just filled -- check DATABASE_URL in .env.

${codes:-The seed printed no label codes this time; its own output above is what it did.}

Those are the two stickers the seed printed, and typing one in is the quickest
way to see what this is for. No camera needed: the app opens with the cursor
in the box marked "Scan or type a code". Enter the first, and it names an item
and adds one of it to the batch. The second names a shelf instead, and sets
where the whole batch is going. guides/volunteer.md walks the rest.
DONE
