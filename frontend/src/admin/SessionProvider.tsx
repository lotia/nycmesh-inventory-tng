/**
 * Who is signed in, shared by every screen that changes what it offers.
 *
 * One read of `/api/me` rather than one per control: the item list, the
 * volunteer picker and the scanner all ask the same question, and asking it
 * four times would be four answers that can disagree while they are in flight.
 */
import { createContext, type ReactNode, useContext } from "react";
import { ANONYMOUS, type Me, useSession } from "../api/capabilities";

// `undefined` is "no provider above me" and `null` is "the provider has one
// and it has not arrived". Two absences, because they need different answers:
// the first is a bug in the tree and throws, the second is a moment every load
// passes through.
const SessionContext = createContext<Me | null | undefined>(undefined);

export function SessionProvider({ children }: { children: ReactNode }) {
  return <SessionContext value={useSession()}>{children}</SessionContext>;
}

/** The context as the provider set it, guarded. Named as a hook because it calls one. */
function useHeldSession(): Me | null {
  const value = useContext(SessionContext);
  if (value === undefined) {
    throw new Error("useCurrentSession must be called inside a <SessionProvider>");
  }
  return value;
}

export function useCurrentSession(): Me {
  // Folded into nobody, because for every screen but the one below "not read
  // yet" and "nobody" want the same answer: no control is drawn either way.
  return useHeldSession() ?? ANONYMOUS;
}

/**
 * The answer if there is one, and null while there is not.
 *
 * PROVISIONAL, and one consumer: the enrolment gate. It is the one place where
 * "not read yet" and "nobody" differ, because it decides whether to mount the
 * app at all, and mounting it on the fallback drew a screenful of refusals
 * before swapping itself in. See `device/Enrolment.tsx`; inventory-tng-81f7.4
 * removes this with it.
 */
export function useSessionAnswered(): Me | null {
  return useHeldSession();
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
