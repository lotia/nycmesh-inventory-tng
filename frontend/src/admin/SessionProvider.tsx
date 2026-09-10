/**
 * Who is signed in, shared by every screen that changes what it offers.
 *
 * One read of `/api/me` rather than one per control: the item list, the
 * volunteer picker and the scanner all ask the same question, and asking it
 * four times would be four answers that can disagree while they are in flight.
 */
import { createContext, type ReactNode, useContext } from "react";
import { type Me, useSession } from "../api/capabilities";

/**
 * The answer, and whether it has arrived yet.
 *
 * THE SECOND HALF EXISTS BECAUSE THE SAFE DEFAULT RUNS BOTH WAYS, which is not
 * obvious and cost a rewrite to notice. For a capability, "not known yet" and
 * "no" want the same treatment: draw nothing, because the server would refuse
 * the operation anyway -- so `useCan` never needs to tell them apart and this
 * flag is deliberately not offered to it.
 *
 * For a way IN, they are opposites. "Not signed in" is not the absence of a
 * control, it is a control reading Sign in, so treating the gap before the
 * answer as "nobody" would show an administrator the wrong control on every
 * page load and then swap it under them. Only a screen with that problem reads
 * `settled`.
 */
interface Session {
  me: Me;
  settled: boolean;
}

const SessionContext = createContext<Session | null>(null);

export function SessionProvider({ children }: { children: ReactNode }) {
  return <SessionContext value={useSession()}>{children}</SessionContext>;
}

/** The context, or a complaint naming the provider that is missing. */
function useSessionContext(): Session {
  const value = useContext(SessionContext);
  if (value === null) {
    throw new Error("The session hooks must be called inside a <SessionProvider>");
  }
  return value;
}

export function useCurrentSession(): Me {
  return useSessionContext().me;
}

/**
 * Whether `/api/me` has answered, however it answered.
 *
 * False covers both "still in flight" and "could not be read". A screen that
 * would otherwise guess wrong draws nothing in both cases, which is the honest
 * answer to a question this client has not had answered.
 */
export function useSessionSettled(): boolean {
  return useSessionContext().settled;
}

/**
 * Whether this session may make one named operation, right now.
 *
 * Only an explicit true draws a control. A capability this server has never
 * heard of, or an answer that arrived in a shape this client does not know,
 * is a no -- the server would refuse the operation anyway, and drawing a
 * control on a guess is the thing decision 0014 point 3 forbids.
 */
export function useCan(capability: string): boolean {
  return useCurrentSession().capabilities?.[capability] === true;
}
