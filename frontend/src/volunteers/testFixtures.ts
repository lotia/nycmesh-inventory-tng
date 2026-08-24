/**
 * The two volunteers the picker tests search for.
 *
 * Sean McGinnis is the name the sheet this project replaces holds several
 * spellings of, which is what the search-before-you-add order exists for.
 */
import type { Volunteer } from "../api/types";

export const sean: Volunteer = {
  id: 7,
  display_name: "Sean McGinnis",
  email: "sean@example.org",
  slack_id: null,
};

export const olivia: Volunteer = { id: 8, display_name: "Olivia", email: null, slack_id: null };

/**
 * The same person as a narrowed payload sends her: a name and an id, and no
 * identifier fields at all.
 *
 * PROVISIONAL, with `ANONYMOUS_PAYLOAD` -- inventory-tng-81f7.4 removes it.
 * Here rather than inline because it is a shape the API can answer with rather
 * than one test's data, and because the property it exists to hold is that
 * this screen does not have to change to render it.
 */
export const namedOnly: Volunteer = { id: 9, display_name: "Priya Raman" };
