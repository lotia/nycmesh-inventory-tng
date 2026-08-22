/**
 * What the volunteer is told about a batch the ledger has, in one place.
 *
 * Two renderings of the same news: the queue draws it as a line of text it has
 * already assembled, and the submit bar draws the advice as a list under the
 * heading. Neither is free to word it differently -- a volunteer who saved in
 * a basement reads the first and one who saved at a desk reads the second, and
 * they are being told the same thing. Kept apart from both for the reason
 * scan/outcome.tsx is kept apart from the four ways a code arrives.
 */
import type { BatchWarning } from "../api/types";

/** Nothing to add: the movements are in and no balance went negative. */
export const RECORDED = "Recorded.";

/** In, and with balances somebody should go and put eyes on. */
export const WORTH_A_COUNT = "Recorded. Worth a stock count:";

/** All of it on one line, for somewhere that cannot draw a list. */
export function recordedInWords(warnings: BatchWarning[]): string {
  if (warnings.length === 0) {
    return RECORDED;
  }
  return `${WORTH_A_COUNT} ${warnings.map((warning) => warning.detail).join(" ")}`;
}
