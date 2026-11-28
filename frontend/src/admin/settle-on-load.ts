/**
 * A page a tap brought up with a button where that tap's finger may have
 * been: a confirmation page's danger button (templates/_confirm.html), or
 * a notice's Bring it back (templates/_notice.html). It marks what an
 * ignored tap focuses `data-settle`: the way to keep things as they are,
 * or the notice's words. As it loads, taps wait a moment (Ali, 2026-11-27,
 * inventory-tng-0c2z; docs/decisions/0019, Double taps).
 */
import { ignoreTaps } from "../shared/settling";

/** Ignores taps for SETTLE_MS if this page marks a `data-settle`. */
export function settleOnLoad(document: Document): void {
  const target = document.querySelector<HTMLElement>("[data-settle]");
  if (target !== null) {
    ignoreTaps(document, () => target);
  }
}
