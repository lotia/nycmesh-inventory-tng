"""gunicorn's own configuration, which exists for one reason so far.

gunicorn sets up its logging before Django is imported, and it logs to handlers
of its own: an access line per request on `gunicorn.access` and its errors on
`gunicorn.error`. Left alone those arrive as plain text beside the application's
JSON, so one stream carries two formats and whatever is parsing it meets a line
it was not written for. `logconfig_dict` is how gunicorn is told to use the
arrangement in `inventory_tng.logs` instead, and the loggers it names are there.

Loaded explicitly -- `gunicorn -c gunicorn.conf.py` -- rather than by gunicorn
finding it in the working directory, so that a command that has stopped reading
it is visible in the command rather than in its absence.

It is also where the OpenTelemetry SDK is started, in `post_fork` below, and
where the access line is given a shape that does not carry personal data.
"""

import sys
from typing import Any

from inventory_tng import redaction
from inventory_tng.logs import from_environment
from inventory_tng.telemetry import start

# The same call Django's settings module makes, so the master and its workers
# cannot disagree about the format, the level or the layout. Reading the five
# variables here with defaults of its own is how one stream came to carry two
# formats -- the master drawing columns while the workers drew JSON.
logconfig_dict, _announcement = from_environment()
if _announcement:
    print(_announcement, file=sys.stderr)

# The one record in this system that no allowlist can reach, because it is a
# message gunicorn assembles rather than a set of fields -- so the format is
# where it is redacted. `redaction` holds it, and the argument for each part.
access_log_format = redaction.access_log_format(redaction.recording())


def post_fork(server: Any, worker: Any) -> None:
    """Start the telemetry SDK in the worker rather than the master.

    Why here rather than anywhere else -- and why the usual reason given for
    it stopped being true -- is on `telemetry.start`.

    `django=False` because gunicorn has not imported the application yet, and
    instrumenting the framework now would configure empty settings rather than
    wait. `wsgi.py` does that half, in the same worker, a moment later.
    """
    start(django=False)
