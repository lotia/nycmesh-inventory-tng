# 0014 — One stylesheet, in NYC Mesh's look, from Tailwind

**Status:** accepted · 2026-11-06 · builds on 0010 (Vite builds the
frontend)

## Context

Administrators should be able to work entirely from a phone, and the pages
should look like MeshDB (<https://db.nycmesh.net/>), NYC Mesh's other app.
Until now each kind of page styled itself: the Jinja pages with a small
stylesheet inline in `admin_base.html`, the scan and registration pages with
`frontend/src/app.css`. Neither handled a narrow screen beyond one column.

MeshDB uses Bootstrap 5.3. What we want from it is its look, not its
software (Ali, 2026-11-06): a grey band (`#f4f4f4`) behind the header and
footer, NYC Mesh's logo, headings in `#55585b` over a rule, rows that turn
yellow (`#f9e79f`) on hover. A framework is welcome if what it ships is
tree-shaken and minified.

## Decision

- **Tailwind CSS v4**, through its Vite plugin, builds one stylesheet,
  `frontend/src/styles.css`, for every page. Tailwind writes only the
  classes it finds in the sources it scans, minified: the frontend's and the
  Jinja templates (`@source`). The scan and registration pages import it;
  it is also an entry of its own, which `admin_base.html` loads through the
  manifest (`bundle.shared_styles`), so both kinds of page share one cached
  file.
- **MeshDB's identity is a handful of tokens** in `@theme`: colours, type.
  Each is a CSS variable, so the dark scheme is the same tokens with other
  values. MeshDB is light only; the dark scheme is ours, following the
  device's setting (Ali, 2026-11-06).
- **Plain elements get type, colour and borders** in the stylesheet's base
  layer, never spacing between them. The administrator pages' form spacing
  belongs to their layout (`.admin`), and the scan pages keep their own, so
  neither has rules to undo the other's. Repeated pieces of markup become
  Jinja macros or Preact components, not copied runs of utility classes.
- **A class name is always written whole** in a template or a component.
  Tailwind finds classes by reading the sources as text, so one built from
  parts, such as `"text-" ~ colour`, is never generated.
- **The header is text, with NYC Mesh's logo** (Ali, 2026-11-06: text
  preferred, and NYC Mesh's logo rather than MeshDB's own). The logo comes
  from MeshDB's repository, which is MIT licensed, and is inlined in the
  page.
- **Development sees changes as they are saved**, from Vite's dev server.
- **Without a build, pages are plain HTML**, unstyled but working. The
  container builds the stylesheet, with the templates copied in for
  Tailwind to read.

We rejected Bootstrap, MeshDB's own choice, because its CSS cannot be
tree-shaken without a separate purge step; Pico CSS, which styles plain
markup but cannot be tree-shaken and gives no shared vocabulary for new
components; and a stylesheet of our own, which would mean writing the
responsive patterns ourselves.

## Consequences

- The Jinja pages now depend on the frontend's build for their look, as the
  scan page already did for its scripts.
- Tailwind is a development dependency of the frontend only. Nothing runs
  in the browser but the CSS it writes.
