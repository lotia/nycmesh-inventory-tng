/**
 * What this session is and what it may do, as the server says.
 *
 * Every administrative control in this app is drawn from what is here, which
 * is decision 0014 point 3; that record says why it is asked rather than
 * guessed at.
 *
 * A capability is what the caller may do *now*. That is not the same as what
 * they are entitled to: a destructive operation also wants a recent sign-in
 * (decision 0014 point 5), so `edit_catalogue` goes false when the session
 * goes stale even though `administrator` stays true. `recently_authenticated`
 * is what tells the two apart -- one is a control to hide, the other a prompt
 * to show somebody who is entitled to it.
 */
import { useResource } from "./useResource";

/**
 * What this deployment asks of the device, and what it has done about it.
 *
 * PROVISIONAL, with everything else the access-posture demo needs; see
 * `device/credential.ts`, and inventory-tng-81f7.4 for what removes it.
 *
 * The server answers this outright rather than leaving the app to work it out
 * from a refusal; `enrolment_state` in the backend's `inventory/permissions.py`
 * is why. `not_required` is what every deployment answers until one sets
 * `VOLUNTEER_ACCESS`.
 */
export type Enrolment = "not_required" | "enrolled" | "self" | "code";

export interface Me {
  authenticated: boolean;
  username: string | null;
  administrator: boolean;
  recently_authenticated: boolean;
  enrolment: Enrolment;
  /**
   * How many characters the pick-list waits for before it answers with rows.
   *
   * PROVISIONAL, with `SEARCH_MINIMUM`; inventory-tng-81f7.4 removes it. Read
   * for the same reason `enrolment` is -- there is a question the answer alone
   * cannot settle, and `CurrentUserView` on the far side names it. Nought is
   * every deployment's answer until one sets the setting.
   */
  search_minimum: number;
  capabilities: Record<string, boolean>;
}

/** Nobody, which is the ordinary case: a volunteer never signs in. */
export const ANONYMOUS: Me = {
  authenticated: false,
  username: null,
  administrator: false,
  recently_authenticated: false,
  // Not `self`, deliberately. This is also the answer while `/api/me` is still
  // in flight or could not be read, and offering to enrol on the strength of a
  // failed read would put an enrolment screen in front of somebody whose only
  // problem was a basement.
  enrolment: "not_required",
  // Nought, so a client that could not read the answer offers to add somebody
  // exactly as it always did rather than refusing to.
  search_minimum: 0,
  capabilities: {},
};

/**
 * Read once, on load.
 *
 * No way to re-read, deliberately: the only thing that changes this answer is
 * signing in or signing in again, both of which are allauth's own pages and
 * both of which come back as a fresh load of the app. Returned as the answer
 * itself rather than wrapped, so the value a context carries is stable between
 * renders and a consumer re-renders when the session changes rather than
 * whenever its provider does.
 */
export function useSession(): Me | null {
  // `useResource` already owns the abort race and the three states; what is
  // different here is only what a failure means. `ANONYMOUS` covers both
  // "nobody" and "could not be read", and it is the right answer to both: no
  // control is drawn, and the server would refuse one anyway.
  //
  // NULL IS "NOT ASKED YET", and it is a third answer rather than a second
  // spelling of nobody. It has one consumer -- the enrolment gate, which
  // decides whether to mount the app at all and mounted it on the fallback,
  // drawing a screenful of refusals before swapping itself in. Every other
  // screen reads through `useCurrentSession`, which folds it back into
  // `ANONYMOUS`. A FAILED read is not null: it will not arrive later, and a
  // screen waiting for it would wait for ever.
  const { data, loading } = useResource<Me>("/api/me");
  return loading ? null : (data ?? ANONYMOUS);
}
