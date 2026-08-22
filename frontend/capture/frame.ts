/**
 * What part of the page a shot is of.
 *
 * A guide's picture is one control, not one screen: a reader looking for the
 * box a code is typed into should not have to find it in a photograph of the
 * whole app. Playwright screenshots an element or a rectangle, and everything
 * here works out that rectangle from the elements it is drawn around -- which
 * is arithmetic, so it is here rather than in the driver, where it would only
 * ever be exercised by taking a picture and looking at it.
 */

/** A rectangle on the page, in CSS pixels: Playwright's own `clip`. */
export interface Box {
  x: number;
  y: number;
  width: number;
  height: number;
}

/**
 * The smallest rectangle holding all of these, plus a margin.
 *
 * The margin is what stops a control being cropped flush against its own
 * border, which reads as a rendering fault rather than as a crop. It is not
 * allowed to push the rectangle off the top or the left of the page, where
 * Playwright refuses a negative origin.
 */
export function union(boxes: Box[], margin = 0): Box {
  const first = boxes[0];
  if (first === undefined) {
    throw new Error("A shot needs at least one element to be drawn around.");
  }
  const left = Math.min(...boxes.map((box) => box.x));
  const top = Math.min(...boxes.map((box) => box.y));
  const right = Math.max(...boxes.map((box) => box.x + box.width));
  const bottom = Math.max(...boxes.map((box) => box.y + box.height));
  const x = Math.max(0, left - margin);
  const y = Math.max(0, top - margin);
  return { x, y, width: right + margin - x, height: bottom + margin - y };
}

/**
 * The same rectangle, ending before `edge`.
 *
 * For the one row in the catalogue that carries a control the reader of the
 * volunteer's guide does not have: Edit is drawn only for somebody signed in
 * as an administrator, and the capture run is signed in as one. Cropping it
 * out shows the row the guide is describing; the administrator's guide shows
 * the button in its own shot.
 *
 * An edge at or beyond the right of the box leaves the box alone, so a
 * control that moves rather than disappearing cannot silently produce a
 * picture of nothing.
 */
export function endingBefore(box: Box, edge: number): Box {
  const width = Math.min(box.width, edge - box.x);
  if (width <= 0) {
    throw new Error(`Nothing is left of ${edge}: the box starts at ${box.x}.`);
  }
  return { ...box, width };
}
