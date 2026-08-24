#!/usr/bin/env bash
# The cases check-telemetry.sh has to get right in both directions.
#
# Weighted towards what it must NOT object to, for the reason
# check-docs.test.sh gives about its own suite: a checker that complains about
# reads, about helpers and about anything that merely sits beside a command is
# one everybody switches off.
#
# Usage: scripts/check-telemetry.test.sh

set -uo pipefail

HERE=$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")
. "$HERE/testlib.sh"
workspace

# The real entry point, in a repository of its own. Driving the reader directly
# would leave the half that decides *what is read* untested -- the corpus, the
# exclusion of the tests, the allowlist's address -- and that half is where a
# checker silently stops checking anything.
scene() {
  new_repo "$WORK/repo"
  mkdir -p "$WORK/repo/scripts" "$WORK/repo/backend/src/inventory/tests"
  cp -f "$HERE/report.sh" "$HERE/check-telemetry.py" "$HERE/check-telemetry.sh" "$WORK/repo/scripts/"
  printf '# The exceptions, none yet.\n' > "$WORK/repo/scripts/check-telemetry.allow"
  cd "$WORK/repo" || exit 1
}

# What is written has to be staged first: the corpus comes from `git ls-files`,
# which is also why no case here ever commits.
staged_check() {
  git -C "$WORK/repo" add -A >/dev/null 2>&1
  (cd "$WORK/repo" && scripts/check-telemetry.sh "$@")
}
check staged_check

# Where a case writes its module. Every one writes exactly one.
SRC=backend/src/inventory

echo "check-telemetry.sh"

scene
cat > "$SRC/view.py" <<'PY'
import structlog
from rest_framework.views import APIView

log = structlog.get_logger(__name__)


class ThingView(APIView):
    def post(self, request):
        log.info("thing recorded")
        return None
PY
expect 0 "says so" "a view that records what it did is left alone"

scene
cat > "$SRC/view.py" <<'PY'
import structlog
from rest_framework.views import APIView

log = structlog.get_logger(__name__)


class ThingView(APIView):
    def post(self, request):
        return None
PY
expect 1 "changes something and says nothing" "a write that says nothing is found"

scene
cat > "$SRC/view.py" <<'PY'
from rest_framework.views import APIView


class ThingView(APIView):
    def post(self, request):
        print("nothing")
        return None
PY
expect 1 "has no logger of its own" "a module with no logger at all is found"

scene
cat > "$SRC/view.py" <<'PY'
import structlog
from rest_framework.views import APIView

log = structlog.get_logger(__name__)


class ThingView(APIView):
    def get(self, request):
        return None
PY
expect 0 "says so" "a read is not a change, and is not asked to say anything"

scene
cat > "$SRC/helper.py" <<'PY'
def stage(rows):
    """A step a command calls. What it did is the command's to report."""
    return len(rows)
PY
expect 0 "says so" "a module that is neither a view nor a command is left alone"

scene
cat > "$SRC/command.py" <<'PY'
from inventory.management.commands import _telemetry


class Command:
    def handle(self, *args, **options):
        with _telemetry.running("thing"):
            pass
PY
expect 0 "says so" "a command reporting through the shared helper counts as saying so"

scene
cat > "$SRC/command.py" <<'PY'
class Command:
    def handle(self, *args, **options):
        pass
PY
expect 1 "has no logger of its own" "a command that says nothing is found"

# DRF splits one write across two methods, so a class that says something on
# either half has said it. The opposite reading asks for two records per edit.
scene
cat > "$SRC/view.py" <<'PY'
import structlog
from rest_framework.generics import RetrieveUpdateAPIView

log = structlog.get_logger(__name__)


class ThingDetailView(RetrieveUpdateAPIView):
    def perform_update(self, serializer):
        super().perform_update(serializer)
        log.info("row edited")

    def update(self, request, *args, **kwargs):
        return super().update(request, *args, **kwargs)
PY
expect 0 "says so" "one write said once, across the two methods that serve it"

scene
cat > "$SRC/view.py" <<'PY'
import structlog
from rest_framework.generics import RetrieveUpdateAPIView

log = structlog.get_logger(__name__)


class ThingDetailView(RetrieveUpdateAPIView):
    def perform_update(self, serializer):
        super().perform_update(serializer)
        log.info("row edited")

    def delete(self, request, *args, **kwargs):
        return None
PY
expect 1 "changes something and says nothing" "and a second path still has to say so for itself"

# A generic view declares nothing and answers a POST anyway, which is how most
# of this repository's writes are written. The reader following a base list is
# what makes these four cases mean anything.
scene
cat > "$SRC/view.py" <<'PY'
import structlog
from rest_framework.generics import ListCreateAPIView

log = structlog.get_logger(__name__)


class ThingListView(ListCreateAPIView):
    queryset = None
PY
expect 1 "handles a write it does not declare" "a declarative view that says nothing is found"

scene
cat > "$SRC/view.py" <<'PY'
import structlog
from rest_framework.generics import ListCreateAPIView

log = structlog.get_logger(__name__)


class RecordsWhatItCreated:
    def perform_create(self, serializer):
        log.info("row added")


class ThingListView(RecordsWhatItCreated, ListCreateAPIView):
    queryset = None
PY
expect 0 "says so" "and one whose mixin speaks for it is left alone"

# The rule that must not go quiet when it stops being able to see.
scene
cat > "$SRC/view.py" <<'PY'
import structlog
from rest_framework.generics import ListCreateAPIView

from inventory.elsewhere import RecordsWhatItCreated

log = structlog.get_logger(__name__)


class ThingListView(RecordsWhatItCreated, ListCreateAPIView):
    queryset = None
PY
expect 1 "cannot be told" "a mixin from another module is said to be out of reach, not passed over"

scene
cat > "$SRC/view.py" <<'PY'
import structlog
from rest_framework.generics import ListAPIView

log = structlog.get_logger(__name__)


class ThingListView(ListAPIView):
    queryset = None
PY
expect 0 "says so" "a base that brings no write is not asked to say anything"

scene
cat > "$SRC/view.py" <<'PY'
import structlog
from rest_framework.views import APIView

log = structlog.get_logger(__name__)


class ThingView(APIView):
    async def post(self, request):
        return None
PY
expect 1 "changes something and says nothing" "an async write that says nothing is found too"

# The corpus leaves the tests out, and check-telemetry.sh says why. This is the
# case that would notice that stopping.
scene
cat > "$SRC/tests/test_thing.py" <<'PY'
from rest_framework.views import APIView


class ThingView(APIView):
    def post(self, request):
        return None
PY
expect 0 "says so" "a test that changes something is not asked to say so"

# The allowlist, and the two ways one goes stale.
scene
cat > "$SRC/view.py" <<'PY'
from rest_framework.views import APIView


class ThingView(APIView):
    def post(self, request):
        return None
PY
printf '%s: a deliberate exception, argued here\n' "$SRC/view.py" >> scripts/check-telemetry.allow
expect 0 "says so" "an allowed module with a reason is excused"

scene
cat > "$SRC/view.py" <<'PY'
from rest_framework.views import APIView


class ThingView(APIView):
    def post(self, request):
        return None
PY
printf '%s\n' "$SRC/view.py" >> scripts/check-telemetry.allow
expect 1 "does not say why" "an allowance with no reason is refused"

scene
cat > "$SRC/helper.py" <<'PY'
def quiet():
    return 1
PY
printf '%s: excusing something that needs no excuse\n' "$SRC/helper.py" >> scripts/check-telemetry.allow
expect 1 "has nothing to excuse" "an allowance that outlived what it excused is found"

# Named paths rather than the default corpus, which is a form the entry point
# offers and nothing here exercised. `check-telemetry.py` says what judging an
# allowance without it cost.
scene
cat > "$SRC/view.py" <<'PY'
import structlog
from rest_framework.views import APIView

log = structlog.get_logger(__name__)


class ThingView(APIView):
    def post(self, request):
        log.info("thing recorded")
        return None
PY
cat > "$SRC/quiet.py" <<'PY'
from rest_framework.views import APIView


class QuietView(APIView):
    def post(self, request):
        return None
PY
printf '%s: a deliberate exception, argued here\n' "$SRC/quiet.py" >> scripts/check-telemetry.allow
assert "$(staged_check "$SRC/view.py")" $? 0 "says so" "one path named, and an allowance for another is not a failure"

# The guard check-telemetry.sh has around the reader, exercised rather than
# asserted: a file that will not parse must not come back green.
scene
cat > "$SRC/view.py" <<'PY'
class ThingView(APIView:
PY
expect 2 "nothing was checked" "a reader that could not read says so instead of passing"

verdict
