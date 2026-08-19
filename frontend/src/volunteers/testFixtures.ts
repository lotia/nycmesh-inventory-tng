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
