#!/usr/bin/env bash
# What bootstrap-dev.sh must do, in what order, and what it must never do.
#
# The one that matters most is the .env case. A script that clobbers the file
# holding somebody's local configuration is worse than no script, and it is a
# fault nobody notices until the run that loses their work.
#
# Every tool the script reaches for is a stub on a PATH of this suite's own, so
# nothing here starts a container, and whether this machine has docker installed
# does not decide what is tested.
#
# Usage: scripts/bootstrap-dev.test.sh

set -uo pipefail

HERE=$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")
. "$HERE/testlib.sh"
workspace

# The script's own external calls, and nothing else, so that "docker is not
# installed" is a state this suite can put it in.
BORROWED=(bash readlink dirname cp cat git ln mkdir cut)

# A recording stand-in. It answers nothing and succeeds, which is all the
# script asks of any of them.
stub() {
  cat > "$WORK/bin/$1" <<'STUB'
#!/usr/bin/env bash
printf '%s %s\n' "${0##*/}" "$*" >> "$LOG"
STUB
  chmod +x "$WORK/bin/$1"
}

# The same, but answering with what the real command prints. Used for the seed,
# whose last line the script has to carry past its own closing message.
speaking_stub() {
  local name=$1 said=$2
  cat > "$WORK/bin/$name" <<STUB
#!/usr/bin/env bash
printf '%s %s\n' "\${0##*/}" "\$*" >> "\$LOG"
printf '%s\n' "$said"
STUB
  chmod +x "$WORK/bin/$name"
}

# And one that refuses, for the failures the script has to explain rather than
# pass through.
failing_stub() {
  cat > "$WORK/bin/$1" <<'STUB'
#!/usr/bin/env bash
printf '%s %s\n' "${0##*/}" "$*" >> "$LOG"
echo "Error response from daemon: Bind for 0.0.0.0:5432 failed" >&2
exit 1
STUB
  chmod +x "$WORK/bin/$1"
}

# A checkout with nothing done to it yet, and a PATH holding only what the
# script is entitled to find. Which stubs exist is the argument.
scene() {
  rm -rf "${WORK:?}/bin"
  mkdir -p "$WORK/bin"
  # A git repository, because one of the steps configures git and the rest of
  # the suite would otherwise be exercising a directory no contributor has.
  new_repo "$WORK/repo"
  mkdir -p "$WORK/repo/scripts" "$WORK/repo/backend" "$WORK/repo/.beads/hooks"
  cp "$HERE/bootstrap-dev.sh" "$WORK/repo/scripts/bootstrap-dev.sh"
  # Sourced by the script under test rather than stubbed, because where the
  # hooks are and whether git is pointed at them is exactly what half of this
  # suite is about. scripts/check-setup.sh sources the same file.
  cp "$HERE/hooks-path.sh" "$WORK/repo/scripts/hooks-path.sh"
  # What the hook is meant to lead to. A stub, because the step under test
  # asks whether the link reaches something git could run, not what it says.
  printf '#!/usr/bin/env bash\n' > "$WORK/repo/scripts/check-commit.sh"
  chmod +x "$WORK/repo/scripts/check-commit.sh"
  printf 'DJANGO_SECRET_KEY=from-the-sample\n' > "$WORK/repo/.env.sample"
  : > "$WORK/log"
  local tool
  for tool in "${BORROWED[@]}"; do
    ln -s "$(command -v "$tool")" "$WORK/bin/$tool"
  done
  for tool in "$@"; do stub "$tool"; done
}

# No GIT_CEILING_DIRECTORIES. It was here, and it was the suite proving a
# property the script did not have: with the search for a repository stopped at
# the workspace, a checkout unpacked inside somebody else's passed a case the
# shipped script failed.
bootstrap() {
  PATH="$WORK/bin" LOG="$WORK/log" "$WORK/repo/scripts/bootstrap-dev.sh" 2>&1
}
check bootstrap

# What the stubs recorded, as one line per call in the order they were made.
log() { cat "$WORK/log"; }

echo "bootstrap-dev.sh"

scene mise docker uv
expect 0 "Over to you" "a clean checkout is bootstrapped"

scene mise docker uv
bootstrap > /dev/null
assert "$(cat "$WORK/repo/.env")" 0 0 "from-the-sample" "a checkout with no .env gets one from the sample"

scene mise docker uv
printf 'DJANGO_SECRET_KEY=mine\n' > "$WORK/repo/.env"
bootstrap > /dev/null
assert "$(cat "$WORK/repo/.env")" 0 0 "mine" "an existing .env is not overwritten"

scene mise docker uv
printf 'DJANGO_SECRET_KEY=mine\n' > "$WORK/repo/.env"
refute "$(bootstrap)" $? 0 "Wrote .env" "and it does not claim to have written one"

# Running it again is the ordinary case, not the exception: a developer comes
# back to a checkout they already bootstrapped and wants the database up.
scene mise docker uv
bootstrap > /dev/null
output=$(bootstrap)
status=$?
assert "$output" "$status" 0 "Left exactly as it was" "a second run leaves the configuration alone"

# Each step reads what the one before it wrote, and the ordering is the whole
# reason this script exists rather than a list in a document.
scene mise docker uv
bootstrap > /dev/null
ORDER=$(log | grep -oE '^(mise install|docker compose up|uv run python src/manage.py (migrate|seed_demo_data))' | tr '\n' ',')
assert "$ORDER" 0 0 "mise install,docker compose up,uv run python src/manage.py migrate,uv run python src/manage.py seed_demo_data," \
  "the toolchain, then the database, then the schema, then the seed"

scene mise docker uv
bootstrap > /dev/null
assert "$(log)" 0 0 "docker compose up -d --wait postgres" "it waits for the database rather than racing it"

# The hooks. A clone carries the files and none of the git configuration that
# makes git run them, so until this step a checkout has a commit checker it
# never consults -- which is the whole of the fault this step was added for.
hooks_path() { git -C "$WORK/repo" config --local --get core.hooksPath || true; }
hook_leads_to() { readlink -f "$WORK/repo/.beads/hooks/commit-msg" || true; }

scene mise docker uv
bootstrap > /dev/null
assert "$(hooks_path)" 0 0 ".beads/hooks" "a checkout git has not been pointed at gets pointed at the hooks"

scene mise docker uv
bootstrap > /dev/null
assert "$(hook_leads_to)" 0 0 "$(readlink -f "$WORK/repo/scripts/check-commit.sh")" \
  "and the commit-msg hook leads to the commit checker"

scene mise docker uv
bootstrap > /dev/null
expect 0 "already points at .beads/hooks" "a second run says so rather than writing it again"

# Two spellings of one directory. beads writes the absolute one, so this is
# what a checkout that has already been through this looks like -- and reading
# it as a clash sent the first real run of this step off to a refusal telling
# the reader to move their hooks into the directory they were already in.
scene mise docker uv
git -C "$WORK/repo" config --local core.hooksPath "$WORK/repo/.beads/hooks"
expect 0 "already points at .beads/hooks" "an absolute hooks path leading to the same place is the same place"

scene mise docker uv
ln -s ../../scripts/check-commit.sh "$WORK/repo/.beads/hooks/commit-msg"
expect 0 "already there" "a hook that arrived with the clone is left as it is"

# Somebody who has pointed git at a hooks directory of their own has made a
# decision, and only one such directory can be in force -- so overwriting it
# would turn theirs off without saying so.
scene mise docker uv
mkdir -p "$WORK/repo/.githooks"
git -C "$WORK/repo" config --local core.hooksPath .githooks
expect 1 "will not overwrite yours" "a hooks directory somebody else chose stops the run"

scene mise docker uv
mkdir -p "$WORK/repo/.githooks"
git -C "$WORK/repo" config --local core.hooksPath .githooks
bootstrap > /dev/null 2>&1
assert "$(hooks_path)" 0 0 ".githooks" "and it is still theirs afterwards"

# A REFUSAL OF ITS OWN when the directory they named is not there, and not the
# one above: telling somebody to move what they keep in a directory that does
# not exist is advice they cannot take. scripts/check-setup.sh reports the same
# state, and the two have to agree that neither will repair it for them.
scene mise docker uv
git -C "$WORK/repo" config --local core.hooksPath .githooks
expect 1 "there is no such directory" "a hooks path leading nowhere is refused on its own terms"

scene mise docker uv
git -C "$WORK/repo" config --local core.hooksPath .githooks
bootstrap > /dev/null 2>&1
assert "$(hooks_path)" 0 0 ".githooks" "and it is still theirs afterwards too"

# A hook git cannot run is a hook git reports as a failed commit, with nothing
# said about why -- so both ways of getting there are answered here instead.
scene mise docker uv
ln -s ../../scripts/gone-away.sh "$WORK/repo/.beads/hooks/commit-msg"
expect 1 "should lead to" "a hook pointing at something else is refused"

scene mise docker uv
chmod -x "$WORK/repo/scripts/check-commit.sh"
expect 1 "not executable" "a checker git could not run is refused"

# An unpacked tarball has no hooks to install and is not a broken checkout.
scene mise docker uv
rm -rf "$WORK/repo/.git"
expect 0 "no hooks to install" "somewhere that is not a git checkout is not treated as a fault"

# And the case the guard is actually asking about, which is this directory
# rather than "am I inside one somewhere". A copy unpacked under a stranger's
# checkout answers yes to the second question, and the harm of acting on that
# answer is spelled out where the guard is.
scene mise docker uv
rm -rf "$WORK/repo/.git"
new_repo "$WORK/outer"
mv "$WORK/repo" "$WORK/outer/repo"
mkdir -p "$WORK/repo"
ln -s "$WORK/outer/repo/scripts" "$WORK/repo/scripts"
bootstrap > /dev/null
assert "$(git -C "$WORK/outer" config --local --get core.hooksPath || echo none)" 0 0 "none" \
  "an unpacked copy inside another repository leaves that repository's hooks alone"
mv "$WORK/outer/repo" "$WORK/repo-back" && rm -rf "$WORK/repo" && mv "$WORK/repo-back" "$WORK/repo"

# core.hooksPath set anywhere is a value git is holding, and a local one
# written over it turns off hooks that were somebody's own -- signing, secret
# scanning, an employer's policy. Reading only the local scope reported that as
# success.
scene mise docker uv
printf '[core]\n\thooksPath = %s/elsewhere\n' "$WORK" > "$WORK/gitconfig"
mkdir -p "$WORK/elsewhere"
output=$(GIT_CONFIG_GLOBAL="$WORK/gitconfig" bootstrap 2>&1)
status=$?
assert "$output" "$status" 1 "in your global configuration" "a hooks path set globally is seen, and named as global"

scene mise docker uv
printf '[core]\n\thooksPath = %s/elsewhere\n' "$WORK" > "$WORK/gitconfig"
mkdir -p "$WORK/elsewhere"
GIT_CONFIG_GLOBAL="$WORK/gitconfig" bootstrap > /dev/null 2>&1
assert "$(git -C "$WORK/repo" config --local --get core.hooksPath || echo none)" 0 0 "none" \
  "and is not quietly overridden by a local one"

# The other way somebody loses hooks they had, and the one the clash guard
# cannot see because nothing about it is written in a setting. bootstrap-dev.sh
# says at that branch what is at stake; what is asserted here is only that it
# opens its mouth.
scene mise docker uv
printf '#!/bin/sh\n' > "$WORK/repo/.git/hooks/pre-commit"
chmod +x "$WORK/repo/.git/hooks/pre-commit"
expect 0 "stop running now" "hooks already installed the ordinary way are not switched off in silence"

scene mise docker uv
expect 0 "beads' five hooks run from here too" "and what else this pointer arms is said, not left to be found"

# The two steps a person has to do themselves, said here rather than left to a
# document the reader has already stopped reading by this point.
scene mise docker uv
expect 0 "createsuperuser" "it says how to make an administrator"

scene mise docker uv
expect 0 "authenticator app" "it says the first sign-in needs a second factor"

scene mise docker uv
expect 0 "http://localhost:5173" "it says where to look"

# Whichever container tool is installed. Podman is not a fallback for people
# who could not get docker working; it is the rootless path.
scene mise podman uv
bootstrap > /dev/null
assert "$(log)" 0 0 "podman compose up" "podman runs the database when docker is absent"

scene mise docker podman uv
bootstrap > /dev/null
refute "$(log)" 0 0 "podman compose" "docker is preferred when both are installed"

scene mise uv
expect 1 "Neither docker nor podman" "with no container tool it says so and stops"

scene docker uv
expect 1 "mise is not installed" "with no mise it says how to get one"

# Nothing after a failed prerequisite: a run that carried on would migrate
# against a database that was never started.
scene mise uv
bootstrap > /dev/null 2>&1
refute "$(log)" 0 0 "mise install" "and it stops there rather than carrying on"

# uv comes from mise, so a shell without mise activated has no uv on PATH and
# the toolchain has to be asked for it by name.
scene mise docker
bootstrap > /dev/null
assert "$(log)" 0 0 "mise exec -- uv run python src/manage.py migrate" "without uv on PATH it goes through mise"

# And the closing message is for the same shell, so what it hands the reader to
# type has to work there too. This is the whole failure: a script that works
# around an unactivated mise for itself and then prints bare `uv` and `npm`
# back.
scene mise docker
expect 0 "mise exec -- uv run python src/manage.py createsuperuser" \
  "and the commands it prints back carry the same prefix"

scene mise docker
expect 0 "mise exec -- npm install" "npm too, which comes from the same place"

scene mise docker
expect 0 "Activate mise" "and it says to fix the cause rather than type the prefix forever"

scene mise docker uv
refute "$(bootstrap)" $? 0 "mise exec --" "an activated shell is given the plain commands"

# The seed prints the codes on the two stickers, and they are the one thing in
# its output worth acting on -- so they have to survive the thirty lines of
# closing message rather than being scrolled off above it.
scene mise docker
speaking_stub uv "Labels to scan or type in: DEM0000001, DEM0000002"
output=$(bootstrap)
status=$?
assert "${output##*Over to you}" "$status" 0 "DEM0000001" "the label codes are repeated after the closing message"

scene mise docker
speaking_stub uv "Labels to scan or type in: DEM0000001, DEM0000002"
expect 0 "Scan or type a code" "and it says what to do with one"

# A port somebody else is already on is the commonest way a first run stops,
# and a raw bind error from the container tool names neither the cause nor the
# two settings that move it. `occupy` holds a real socket, because what the
# script asks is whether a connection to that port succeeds.
# Sets PORT to a port it is holding open, and LISTENER to the process holding
# it. Not printed, because a command substitution would put the background
# process in a subshell and lose the pid that has to be killed afterwards.
occupy() {
  : > "$WORK/port"
  python3 -c '
import socket, sys, time
s = socket.socket()
s.bind(("127.0.0.1", 0))
s.listen(1)
open(sys.argv[1], "w").write("%d\n" % s.getsockname()[1])
time.sleep(60)
' "$WORK/port" &
  LISTENER=$!
  local i
  for ((i = 0; i < 50; i++)); do
    [[ -s "$WORK/port" ]] && break
    sleep 0.1
  done
  PORT=$(cat "$WORK/port")
}

free_port() {
  python3 -c 'import socket
s = socket.socket()
s.bind(("127.0.0.1", 0))
print(s.getsockname()[1])
s.close()'
}

scene mise docker uv
failing_stub docker
occupy
printf 'POSTGRES_PORT=%s\n' "$PORT" > "$WORK/repo/.env"
output=$(bootstrap)
status=$?
kill "$LISTENER" 2>/dev/null
assert "$output" "$status" 1 "POSTGRES_PORT=5433" "a port already held is explained rather than passed through"

scene mise docker uv
failing_stub docker
occupy
printf 'POSTGRES_PORT=%s\n' "$PORT" > "$WORK/repo/.env"
output=$(bootstrap)
status=$?
kill "$LISTENER" 2>/dev/null
assert "$output" "$status" 1 "DATABASE_URL=postgres://inventory:inventory@localhost:5433" \
  "and it names both settings, because moving one alone aims Django at the stranger's cluster"

# The other half of the same branch: a database that will not start for some
# other reason must not be told it has a port clash it does not have.
scene mise docker uv
failing_stub docker
printf 'POSTGRES_PORT=%s\n' "$(free_port)" > "$WORK/repo/.env"
output=$(bootstrap)
status=$?
refute "$output" "$status" 1 "POSTGRES_PORT=5433" "a failure with the port free is not blamed on the port"

verdict
