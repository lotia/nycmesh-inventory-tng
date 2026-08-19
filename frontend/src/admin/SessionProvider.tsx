/**
 * Who is signed in, shared by every screen that changes what it offers.
 *
 * One read of `/api/me` rather than one per control: the item list, the
 * volunteer picker and the scanner all ask the same question, and asking it
 * four times would be four answers that can disagree while they are in flight.
 */
import { createContext, type ReactNode, useContext } from "react";
import { type Me, useSession } from "../api/capabilities";

const SessionContext = createContext<Me | null>(null);

export function SessionProvider({ children }: { children: ReactNode }) {
  return <SessionContext value={useSession()}>{children}</SessionContext>;
}

export function useCurrentSession(): Me {
  const value = useContext(SessionContext);
  if (value === null) {
    throw new Error("useCurrentSession must be called inside a <SessionProvider>");
  }
  return value;
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
