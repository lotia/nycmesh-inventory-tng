/**
 * Which pictures the guides are made of, named once.
 *
 * The driver beside this file takes them and `shots.test.ts` checks that each
 * one is committed and that the guide claiming it points at it. Two readers of
 * one list, so a shot renamed in the driver alone fails the build rather than
 * leaving a broken image in a document nobody rebuilds.
 */

/** The two documents in guides/, by the stem of their file name. */
export type Guide = "volunteer" | "administrator";

export interface Shot {
  /** The stem of the PNG, and what the driver calls the step. */
  name: string;
  /** Which guide draws it. */
  guide: Guide;
}

/** Where the PNGs are committed, from the root of the repository. */
export const IMAGE_DIR = "guides/images";

/** Where one guide lives, from the root of the repository. */
export function guidePath(guide: Guide): string {
  return `guides/${guide}.md`;
}

/** Where one shot is committed, from the root of the repository. */
export function imagePath(name: string): string {
  return `${IMAGE_DIR}/${name}.png`;
}

/**
 * Every picture, in the order the driver takes it.
 *
 * The order is the order of the flow, not of the document: one run of the app
 * produces the volunteer's shots from the first Save to the last, and the
 * administrator's from the pages a browser is pointed at afterwards.
 */
export const SHOTS: Shot[] = [
  { name: "volunteer-who-you-are", guide: "volunteer" },
  { name: "volunteer-scan-box", guide: "volunteer" },
  { name: "volunteer-added", guide: "volunteer" },
  { name: "volunteer-catalogue-row", guide: "volunteer" },
  { name: "volunteer-batch", guide: "volunteer" },
  { name: "volunteer-line-refused", guide: "volunteer" },
  { name: "volunteer-worth-a-count", guide: "volunteer" },
  { name: "volunteer-held", guide: "volunteer" },
  { name: "volunteer-outbox", guide: "volunteer" },
  { name: "administrator-identifiers", guide: "administrator" },
  { name: "administrator-item-flag", guide: "administrator" },
  { name: "administrator-volunteers-flagged", guide: "administrator" },
  { name: "administrator-merge", guide: "administrator" },
  { name: "administrator-locations", guide: "administrator" },
  { name: "administrator-label", guide: "administrator" },
  { name: "administrator-label-sheet", guide: "administrator" },
  { name: "administrator-ledger", guide: "administrator" },
];

/** The shots one guide draws, in the order above. */
export function shotsFor(guide: Guide): Shot[] {
  return SHOTS.filter((shot) => shot.guide === guide);
}
