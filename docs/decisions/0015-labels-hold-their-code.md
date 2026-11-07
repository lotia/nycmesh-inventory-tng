# 0015 — A label's QR code holds its code, and the camera reads it

**Status:** accepted · 2026-11-06 · builds on 0010 (the decoder) and 0009
(registered devices)

## Context

Labels name an item, a package of one, or a place, by a code of ten
characters from an alphabet that is hard to misread (`models.mint_label_code`).
The scan page already reads a code a scanner gun types. Volunteers now scan
with a phone's camera too (inventory-tng-8i4.13.7), and labels will be
printed from an admin page (inventory-tng-8i4.16). Today's stickers hold
prefilled Google Form links, which the new app cannot read.

## Decision

- **A label's QR code holds its code and nothing else** (Ali, 2026-11-06),
  never a link. Labels then name nothing about where the app runs: a move to
  another address needs no reprinting. A phone's own camera app shows the
  code as text; volunteers scan from the scan page.
- **A full QR code, never a Micro QR.** segno makes a short text a Micro QR
  unless told otherwise (`segno.make_qr`), and not every reader reads one.
- **QR codes only.** Manufacturers' barcodes would need a field of their own
  on items, so they wait for an issue of their own.
- **One current code for each item, package or place** (Ali, 2026-11-07),
  printed in as many copies as wanted; a unique index keeps it one.
  Separate codes per sticker would add nothing a scan can use. Revoking a
  code voids every copy, and labels made after get a new code.
- **Twelve labels to a page** (Ali, 2026-11-07), three by four, on plain US
  Letter or A4, cut apart or on full-sheet sticker paper: any printer, no
  particular label product.
- **Old stickers are replaced before launch** (Ali, 2026-11-06); one scanned
  reads as a label the inventory does not have.
- **Scanning stays open until Done.** Each item label adds one, or one
  package; a label held in view counts once, and again only after two
  seconds out of view. A place's label asks From or To, offering the side
  still empty first; both then stay set for the batch. Where the phone
  allows (not iOS Safari) a scan buzzes; it always says what it did.
- **The camera is the rear one**, with a torch button where it has one. A
  camera refused or missing says so, and the lists still work.

## Consequences

- Labels are made from an item's or a place's page and found, printed
  again and revoked on the labels page (inventory-tng-8i4.16). Each shows
  the bare code as a full QR code, the code in words beneath it for typing,
  and what it names.
- Chromium's fake camera, showing a seeded label, tests scanning end to end
  (`frontend/browser/camera.spec.ts`); iOS Safari is checked by hand.
