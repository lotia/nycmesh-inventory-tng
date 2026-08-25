/**
 * A write that can be refused, and the three things every screen does about it.
 *
 * Five administrative screens send one write and have to say the same things
 * about it: that it is in flight, so the control cannot be pressed twice; that
 * it was refused, in whichever of the two ways decision 0014 point 5 makes
 * different; and that the refusal can be put away. Written out five times, the
 * part that got retyped was the catch, which is the part nobody re-reads --
 * `inventory-tng-qkiz.1` is where that was noticed and what it had cost.
 *
 * WHAT IT DOES NOT TAKE is what the write is, or what to do afterwards. Those
 * differ at every one of the five and are the reason they are five components:
 * one closes a dialog, one closes a panel and hands back the row it made, one
 * clears a question it had asked itself. So `run` takes the body and gets out
 * of the way, and the tail stays where it is written now.
 *
 * A .tsx rather than a .ts because `refusal` is drawn here. That is the point:
 * how a refusal looks is not a decision five screens should each be making, and
 * `Refusal` is already the one place it is made.
 */
import type { ReactNode } from "react";
import { useState } from "react";
import { type ApiError, asApiError } from "../api/client";
import { Refusal } from "./Refusal";

export interface Saving {
  /** True while the write is in flight; the control it belongs to is disabled. */
  saving: boolean;
  /** What to draw about the last refusal, or null if there was none. */
  refusal: ReactNode;
  /**
   * Send it, and hold on to whatever comes back instead.
   *
   * The body runs only as far as its first failure, so a caller's own tail --
   * closing, handing back what was saved -- is reached only on success, which
   * is what makes writing that tail inline safe.
   */
  run: (write: () => Promise<void>) => Promise<void>;
}

export function useSaving(): Saving {
  const [saving, setSaving] = useState(false);
  const [refused, setRefused] = useState<ApiError | null>(null);

  async function run(write: () => Promise<void>): Promise<void> {
    setSaving(true);
    setRefused(null);
    try {
      await write();
    } catch (error: unknown) {
      setRefused(asApiError(error));
    } finally {
      setSaving(false);
    }
  }

  return {
    saving,
    refusal: refused ? <Refusal error={refused} onDismiss={() => setRefused(null)} /> : null,
    run,
  };
}
