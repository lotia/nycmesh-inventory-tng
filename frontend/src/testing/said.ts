/** What the page says, as text for the tests. Never bundled. */
import type { Said } from "../shared/outbox-notes";

/** A batch's problems, or a quantity's, as text, each name the page quoted
 * in it (outbox-notes.Said) in [brackets]. */
export function saidAsText<T extends object>(result: T) {
  const text = (said: Said) =>
    said
      .map((part) => (typeof part === "string" ? part : `[${part.quoted}]`))
      .join("");
  if ("problems" in result) {
    return { problems: (result.problems as Said[]).map(text) };
  }
  return "problem" in result
    ? { problem: text(result.problem as Said) }
    : result;
}

/** UPGRADE_TO_SAVE as shown, its link's words in it. */
export const UPGRADE_WORDS = "Scanner updated. Upgrade to keep saving.";
