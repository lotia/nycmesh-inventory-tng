/**
 * Which pictures the guides are made of, named once.
 *
 * The driver beside this file takes them and `shots.test.ts` checks that each
 * one is committed and that the guide claiming it points at it. Two readers of
 * one list, so a shot renamed in the driver alone fails the build rather than
 * leaving a broken image in a document nobody rebuilds.
 *
 * Every path this file hands out is relative to the repository, and the one
 * absolute thing they are joined to is below.
 */

import { existsSync } from "node:fs";
import { dirname, resolve } from "node:path";

/**
 * Where the repository is, ending in a separator.
 *
 * Searched for rather than counted to. A fixed number of `..` off the working
 * directory is right only while every reader is started from `frontend/`, and
 * the capture config can be handed to Playwright from the repository root just
 * as easily -- where the same arithmetic lands a directory above the checkout
 * and every read fails naming a path nobody typed. Walking up until the guides
 * are underfoot is right from either.
 *
 * Not from this file's own URL, which would settle it outright under Node.
 * Vitest transforms a module before it loads it, and `import.meta.url` is
 * then not a `file:` URL at all -- which rules the trick out for a file two
 * of the readers below reach through Vitest.
 */
function repositoryRoot(): string {
  let here = resolve(process.cwd());
  while (!existsSync(`${here}/guides`)) {
    const above = dirname(here);
    if (above === here) {
      throw new Error(`No guides/ above ${process.cwd()}: this is not a checkout of it.`);
    }
    here = above;
  }
  return `${here}/`;
}

export const REPO_ROOT = repositoryRoot();

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
  { name: "volunteer-measured-amount", guide: "volunteer" },
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
