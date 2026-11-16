# 0022 — The words people read, through Babel and Lingui

**Status:** accepted · 2026-11-15 · builds on 0002 (server-rendered pages
and one scan page) and 0010 (how the scan page is built)

## Context

Every word an administrator or a volunteer reads was written where it is
used: notices and rules in Python dicts, sentences in Jinja templates, the
scan page's words in its TypeScript. The same phrase was often written in
more than one file, plurals were built by hand in English, and some
sentences were glued from fragments no translation could reorder. Before
the app can speak another language (inventory-tng-ist.2), each side needs
its words in one catalogue, read through a library that handles plurals,
contexts and, later, numbers and dates. A count found about 1,100 phrases:
about 540 in templates, 380 in Python and 200 on the scan page.

The research (inventory-tng-oic) weighed Babel with Jinja's i18n extension
and Fluent on the server; Lingui, FormatJS's intl-messageformat, i18next,
Fluent and MessageFormat 2 on the scan page; and one plain module of
phrases per side, with a library later.

## Decision

Ali decided on 2026-11-15:

- **An established library now**, not plain modules of phrases: a library
  later would touch every call site and every plural again.
- **The server: Babel and Jinja's i18n extension.** gettext catalogues in
  PO files, `pybabel` to extract from Python and templates alike. Jinja's
  extension is newstyle, so a message's values are escaped and filled
  inside it, and trimmed, so a message wrapped over lines reads as one.
  Babel also has the locale data for numbers, dates and units that
  ist.2 will need. Fluent's Python runtime (last released March 2023, with
  no Jinja integration) was rejected as stale.
- **The scan page: Lingui's core**, with no React bindings. Messages are
  ICU MessageFormat in PO catalogues, compiled at build time, so the
  browser compiles nothing and evaluates nothing (the
  Content-Security-Policy allows no `eval`). intl-messageformat parses at
  run time unless compiled first, i18next is about six times the size, and
  Fluent's and MessageFormat 2's libraries are inactive on one side or the
  other.
- **PO on both sides**, so translators have one file format and one tool,
  though plural syntax differs inside: gettext's forms on the server,
  ICU's on the page. The sides keep separate catalogues.
- **English source text is each message's id**, as gettext and Lingui
  both do by default. A message has a context only where the same English
  means two things, from a short fixed list in CONTRIBUTING.md (Phrases):
  From and To as places or as a mail's headers, and open as a thing to do
  or a state. A translator comment goes where the English could be misread.
  Symbolic ids were rejected: Babel and Jinja are built on source text, a
  symbolic id needs an English catalogue kept by hand, and templates would
  read as keys. The cost: any change to English wording retires the old
  message, and its translations come back marked fuzzy, to check.
- **Phrases are named as code symbols**, so tests import a phrase rather
  than repeating its words: a lazy constant on the server
  (`lazy_gettext`), said in the locale active when it is shown, or a
  function for a plural or a phrase with values; an exported descriptor on
  the scan page (Lingui's `msg`). Template words stay in the template, in
  `{% trans %}`.
- **What is in:** every page's words, mail included. **What is out:** the
  operator commands (admins, registration codes, scan settings, unit
  readings, TOTP keys, seed), left for inventory-tng-8f4; comments and
  docstrings. OpenAPI and metric descriptions stay out too, as the
  research proposed.
- **The scan page owns the words for the server's batch problems**, keyed
  by the problem's kind with the values the message needs. The server sends
  kinds, not sentences, and words only its own pages and mail.

Choices made in building it, on 2026-11-15, where the decisions above left
them open:

- The server reads its PO catalogue as it starts, compiled in memory by
  Babel, so no compiled MO file is kept or built. The scan page's are
  compiled by Lingui's Vite plugin as the build imports them, and its
  macros turned into plain descriptors by the plugin's own transform,
  without Babel. So there is no separate compile step, only
  `mise run extract-catalogues`.
- A component on the scan page reads a phrase through a signal
  (`i18n.ts`, `say`), so it renders again when the locale changes.
- On the scan page, a sentence holding a name in bold, a name kept apart
  from the page's own words, or a link is still one message, said in
  parts (`i18n.ts`, `sayParts`): each such value, and the words a link
  marks as `<0>…</0>`, goes where the message places it, so a translation
  may move it (2026-11-16).
- The app's name, NYC Mesh Inventory, is a name and not a phrase
  (`phrases.BRAND`), kept out of the catalogue: a message takes it as a
  value. A name reads the same in every language, and authenticator apps
  show it as the issuer of the account's key, which must not change with
  the reader's language.
- Templates are extracted as newstyle gettext, as they are rendered, so a
  literal per cent is `%%` in their messages too. A sentence with a link,
  or with words only a screen reader hears, is one message, its markup in
  it; the values filled into it are escaped, never marked safe. A sentence
  with several counts says each as a plural of its own, filled into it.
- A refused batch's 422 is `{"detail": [{"type": kind, "loc": [...]}]}`,
  FastAPI's shape without its `msg`, documented in the OpenAPI schema as
  `BatchRefusedReply`: no kind takes values yet, so none are sent. The
  scan page's outbox keeps each problem's kind, never words, so a refusal
  kept is worded in the language spoken when it is shown; a kind it has
  no words for is "Not accepted", as is FastAPI's own 422 for a body
  that cannot be a batch at all (`BodyRefusedReply` in the schema),
  except the two kinds of a line's package size, which it words. That
  problem still names its line. The server says these kinds nowhere
  itself, so it keeps no words for them (2026-11-16).
- Catalogues name the file each message comes from, not its line, so a
  phrase moving within its file leaves them unchanged.
- `mise run check-catalogues`, and CI's job of that name, fail when either
  side's catalogue is not what extracting from the code would write.
- A pseudo-locale, every letter accented, tests each side: what has moved
  into the catalogue is rendered in it, and any word still in plain
  English was not read through the catalogue. Each part that moves words
  adds what it moved, until every page is covered.

## Consequences

- A phrase is added in the code, then the catalogues are extracted again
  (CONTRIBUTING.md, Phrases); the check fails until they are.
- Python formats a message's values the way Jinja's newstyle gettext does,
  always, so a literal per cent sign is written `%%` on the server.
- Until locales are chosen (ist.2), only English is spoken, and one
  catalogue is active for the whole process (`phrases.speaking` switches
  it for tests). Choosing a locale per request will make that a
  context-local setting.
- The scan page's bundle holds the compiled English catalogue; Lingui's
  core adds about 2.5 kB, gzipped.
