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

export interface Me {
  authenticated: boolean;
  username: string | null;
  administrator: boolean;
  recently_authenticated: boolean;
  capabilities: Record<string, boolean>;
}

/** Nobody, which is the ordinary case: a volunteer never signs in. */
const ANONYMOUS: Me = {
  authenticated: false,
  username: null,
  administrator: false,
  recently_authenticated: false,
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
export function useSession(): Me {
  // `useResource` already owns the abort race and the three states; what is
  // different here is only what a failure means. Null covers both "not read
  // yet" and "could not be read", and nobody is the right answer to both: no
  // control is drawn, and the server would refuse one anyway.
  const { data } = useResource<Me>("/api/me");
  return data ?? ANONYMOUS;
}
