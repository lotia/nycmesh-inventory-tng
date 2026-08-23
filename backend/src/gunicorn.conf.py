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

This file is where the OpenTelemetry SDK will be started, in a `post_fork`
hook: gunicorn pre-forks, and an exporter's thread does not survive fork, so an
SDK started in the master leaves every worker buffering spans and exporting
none. That is inventory-tng-nb8.2 and is not here yet.
"""

import sys

from inventory_tng.logs import from_environment

# The same call Django's settings module makes, so the master and its workers
# cannot disagree about the format, the level or the layout. Reading the five
# variables here with defaults of its own is how one stream came to carry two
# formats -- the master drawing columns while the workers drew JSON.
logconfig_dict, _announcement = from_environment()
if _announcement:
    print(_announcement, file=sys.stderr)
