import { readFileSync } from "node:fs";
import { type Guide, guidePath, REPO_ROOT } from "./shots";

/**
 * The controls a guide names, read out of the guide itself.
 *
 * A picture going stale is caught by regenerating it. A *name* going stale is
 * not: rename a button and the guide keeps telling a volunteer at a shelf to
 * press something that is not there, with every test green. The list of what
 * to look for therefore comes from the guide and not from a list beside it,
 * because a list beside it is the thing that would fall behind.
 *
 * The signal is the one the guides already use: a control is written in bold.
 * Bold also carries emphasis, so the two are told apart by shape rather than
 * by an exception list -- a control's name is short and is not a sentence,
 * where an emphasised passage is a clause with punctuation in it. That is a
 * judgement, and it errs towards reading too few: a control named in five
 * words or more is not checked, and nothing breaks if the guides gain one.
 *
 * `integration/guide-controls.spec.ts` is what these are held against.
 */

/** The longest a bold span may be and still be read as a control's name. */
export const MOST_WORDS = 4;

/**
 * Bold, across line breaks: a guide wraps its prose, so a name can be split
 * over two lines. Lazy, so that two spans on one line stay two spans.
 */
const BOLD = /\*\*(.+?)\*\*/gs;

/** A span that ends a sentence or a lead-in is prose, not a name. */
const ENDS_A_SENTENCE = /[.:]$/;

/** Punctuation inside a span says the same thing. */
const IS_A_CLAUSE = /[,;]/;

/** One guide, as it is committed. Nothing outside this file wants the text. */
function readGuide(guide: Guide): string {
  return readFileSync(`${REPO_ROOT}${guidePath(guide)}`, "utf8");
}

/**
 * Every control this text names, once each, in the order it names them.
 */
export function controlsNamed(markdown: string): string[] {
  const found: string[] = [];
  for (const [, span] of markdown.matchAll(BOLD)) {
    const name = span.replace(/\s+/g, " ").trim();
    if (ENDS_A_SENTENCE.test(name) || IS_A_CLAUSE.test(name)) {
      continue;
    }
    if (name.split(" ").length > MOST_WORDS || found.includes(name)) {
      continue;
    }
    found.push(name);
  }
  return found;
}

/** Every control one guide names. */
export function controlsIn(guide: Guide): string[] {
  return controlsNamed(readGuide(guide));
}
