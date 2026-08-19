/**
 * What every code does, wherever it came from.
 *
 * Four ways in -- a label's own deep link, the camera, a scanner gun and
 * somebody typing what is printed under a dead QR -- and they mean the same
 * thing, so they must behave the same way. `applyCode` already made the
 * resolution one path; this makes everything downstream of it one path too:
 * the outcome that is announced, the question a measured item asks, and the
 * beep that says the batch took it.
 *
 * It also owns the one thing a caller cannot: deciding whether a code is a new
 * scan at all. The reducer debounces repeat decodes of the same label
 * (SCAN_DEBOUNCE_MS in cart/cartState.ts), but only for codes that reach it --
 * and a measured item deliberately does not, because it is asking how much
 * first. A camera reads five times a second, so without this the keypad would
 * reopen under the volunteer's finger.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { useCart } from "../cart/CartProvider";
import { SCAN_DEBOUNCE_MS } from "../cart/cartState";
import { applyCode, type Measured, type Outcome, recordMeasured } from "./applyCode";
import { confirmScan } from "./confirm";

/**
 * How long after a code reaches the batch the confirmation is still owed.
 *
 * The reducer is what decides a scan happened, and it says so by changing the
 * cart -- which is a render later. This is the window in which that change is
 * this scan's rather than a stepper edit somebody made in between.
 */
const CONFIRM_WINDOW_MS = 1_000;

export interface ScannedCode {
  /** The last thing that happened, or null once dismissed. */
  outcome: Outcome | null;
  /** A measured item waiting for somebody to say how much. */
  measured: Measured | null;
  /** Hand a code in. Safe to call several times a second. */
  scan: (code: string, signal?: AbortSignal) => void;
  /** The amount, once entered. */
  enter: (quantity: number) => void;
  /** Put the announcement away, and abandon a measured scan with it. */
  dismiss: () => void;
}

export function useScannedCode(): ScannedCode {
  const { cart, dispatch } = useCart();
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const lastScan = useRef({ code: "", at: 0 });
  const confirmUntil = useRef(0);

  // Derived, not held: a measured item is one shape an outcome takes, and two
  // states kept in lockstep are two states that can come apart.
  const measured = outcome?.applied === "measured" ? outcome.measured : null;

  const scan = useCallback(
    (code: string, signal?: AbortSignal) => {
      const trimmed = code.trim();
      const now = Date.now();
      // The same window the reducer applies, applied before resolution so it
      // covers the measured item too -- and so a camera holding one label in
      // frame costs one round trip rather than five a second.
      if (
        trimmed === "" ||
        (trimmed === lastScan.current.code && now - lastScan.current.at < SCAN_DEBOUNCE_MS)
      ) {
        return;
      }
      lastScan.current = { code: trimmed, at: now };
      applyCode(trimmed, dispatch, signal)
        .then((applied) => {
          setOutcome(applied);
          // Only a code that reached the batch has anything to confirm. A
          // measured one has not: it is still being asked about.
          if (applied.applied === "item" || applied.applied === "location") {
            confirmUntil.current = Date.now() + CONFIRM_WINDOW_MS;
          }
        })
        .catch(() => {
          // applyCode answers rather than throws; only an abort reaches here,
          // and an abandoned scan has nothing to say. The code is left in
          // `lastScan` deliberately -- it was asked about.
        });
    },
    [dispatch],
  );

  const enter = useCallback(
    (quantity: number) => {
      if (measured === null) {
        return;
      }
      setOutcome(recordMeasured(measured, quantity, dispatch));
      confirmUntil.current = Date.now() + CONFIRM_WINDOW_MS;
    },
    [measured, dispatch],
  );

  const dismiss = useCallback(() => setOutcome(null), []);

  // The cart is what says a scan landed, so the beep follows the cart rather
  // than the decode: no second debounce here, and no beep at an edit no scan
  // caused. See CONFIRM_WINDOW_MS.
  const { lines, locationId } = cart;
  // Both dependencies are triggers rather than values: neither is read below,
  // and the rule counts that as surplus -- but dropping them is what would be
  // wrong, because "the cart changed" is the entire condition this effect
  // exists to notice.
  // biome-ignore lint/correctness/useExhaustiveDependencies: named to re-run on a cart change, not read
  useEffect(() => {
    // Runs only when one of these actually changed -- which for `lines` means
    // the reducer built a new array, and a debounced repeat does not. On mount
    // the deadline is zero, so nothing is owed.
    if (Date.now() < confirmUntil.current) {
      confirmUntil.current = 0;
      confirmScan();
    }
  }, [lines, locationId]);

  return { outcome, measured, scan, enter, dismiss };
}
