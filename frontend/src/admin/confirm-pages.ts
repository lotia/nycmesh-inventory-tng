/**
 * A confirmation page (backend templates/_confirm.html) is a question a
 * tap brought up, its danger button perhaps where that tap's finger was:
 * as it loads, taps wait a moment, and an ignored one focuses the way to
 * keep things as they are, the safe choice, marked `data-keep` (Ali,
 * 2026-11-27, inventory-tng-0c2z; docs/decisions/0019, Double taps).
 */
import { ignoreTaps } from "../shared/settling";

/** Ignores taps for SETTLE_MS if this page asks to confirm. */
export function settleConfirmPage(document: Document): void {
  const keep = document.querySelector<HTMLElement>("[data-keep]");
  if (keep !== null) {
    ignoreTaps(document, () => keep);
  }
}
