# What CI proves

Some of what this repository's documents claim is executed on every push, and
the rest is not. Which is which is worth knowing before you rely on either.
The tests themselves, and how to run them, are
[Testing and coverage](testing.md).

**The guide's setup is run, not read.** The `Setup instructions` job starts a
clean runner, installs mise off the line in [Prerequisites](../DEVELOPERS.md#prerequisites),
and activates it with the line
[Activate mise, then open a new shell](../DEVELOPERS.md#activate-mise-then-open-a-new-shell)
tells you to add — nothing else, and no shim directory bolted onto the path
behind your back. From there it types what the guide prints and only that:
`scripts/bootstrap-dev.sh`, then a bare `uv` and a bare `npm`, with no prefix
a reader of the guide would not have. A job reaching for
`mise exec --` instead would be a green tick over a command nobody reading
the guide would type, which is worse than no job at all.

Then it asks whether any of it worked: the seed has to have left rows behind
in the catalogue, the labels, the places, the people and the ledger, and the
two servers — started with the two lines the bootstrap script signs off with —
have to answer a request. That is the sentence at the top of [the guide](../DEVELOPERS.md) being
kept rather than repeated.

**The quickstart is run too.** The `Compose stack` job is
[README](../README.md#quickstart) followed by somebody who has only Docker: it
copies `.env.sample`, brings the three services up, asks each of them for a
page, and then types the two commands that page calls not optional. Nothing
else finds out whether the hardening every service declares lets the stack
serve anything, or whether the seed's own refusal is satisfied by the file the
quickstart tells you to copy.

**Deployment is rendered, and what it renders is put to the application.** No
cluster exists in CI, so the `Helm chart` job runs the `helm lint` and
`helm template` commands
[deployment](deployment.md#from-an-empty-cluster-to-a-first-sign-in)
prints — including the administrative ingress, which the default render does
not draw. Rendering is not the whole of it:
`backend/src/inventory/tests/test_chart.py` renders the chart in the `Backend`
job, takes the request a probe would make and the environment the same
manifest supplies, and asks Django what it answers. A manifest that is valid
YAML and describes a pod this application would refuse is the failure that
suite exists for, and it is one `helm lint` cannot see. Everything from the
install onwards is still unproven, and that document says so where it asks you
to type it.

**A command any document names has to exist.**
`backend/src/inventory/tests/test_documented_commands.py` holds every
`manage.py` subcommand, `npm run` script, file under `scripts/` and chart value
the documents mention against what the repository actually has, and names the
file and the line of anything stale. It costs nothing and it catches the
commonest rot there is, which is a rename.

**And a command this repository has must be named somewhere.** The same file
asks it the other way about, which is the direction that actually rots: a
`manage.py` subcommand or an `npm run` script *added* and never written up
keeps every other check green. A couple of them genuinely want no write-up, and
`backend/src/inventory/tests/undocumented.allow` is where saying so goes — its
header says how an entry is written, and an entry that stops being needed is
reported rather than left lying.

**And every page under `docs/` has a row in the guide's outline.** The same
file reads the table under [Everything else](../DEVELOPERS.md#everything-else)
and holds it against the directory in both directions: a page with no row is
a topic with a home nobody is told about, and a row whose page has moved is
the outline lying. A row naming a directory covers what is under it, which is
how the decision records and the briefs, indexed on their own, are excused.

**And CI activates mise with the line the guide prints.** The `Setup instructions`
job's whole claim is that it types what the guide prints, which rests on the
one line in
[Activate mise, then open a new shell](../DEVELOPERS.md#activate-mise-then-open-a-new-shell).
That line is retyped in the workflow rather than shared with anything, so the
same file compares the two and fails if they have drifted apart.

**A control either guide names has to be on the screen.** Each guide keeps one
typographic promise to its reader: a thing you press or type into is set in
bold, and the screen's own words back to you are in italics. That promise is
also what makes the guides machine-readable, so nothing lists the controls
twice — `frontend/capture/controls.ts` reads the short bold phrases out of the
guide itself, and `frontend/integration/guide-controls.spec.ts` walks the scene
the pictures are taken from and fails naming whatever the app no longer offers.

Each guide is held to the screens it is about, and not to the union of both:
the volunteer's to the app, the administrator's to the app and to `/admin/`,
because its first section says it is about the two of them. Pooling them would
let a field on a Django page answer for a button a volunteer is told to press.
The walk has to work for its names — a menu's choices exist only while the menu
is open, and the box asking who you are is gone the moment you answer it — so
it opens what it must and harvests before it moves on.

Comparing regenerated PNGs would catch more and would also fail on a font or a
shadow; this fails on the change that would actually mislead somebody.

What none of it can see is whether a guide has gone on describing a job nobody
does any more, or stayed quiet about one that has appeared. Somebody has to
read them, which is why that is in the [Definition of Done](../DEVELOPERS.md#definition-of-done)
instead.

