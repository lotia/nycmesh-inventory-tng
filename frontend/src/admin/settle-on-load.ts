/**
 * A page a tap brought up, which may put a button where that tap's finger
 * was (Ali, 2026-11-27, inventory-tng-0c2z; docs/decisions/0019, Double
 * taps): one answering a form, whether sent back by its POST (the server
 * marks `<html data-settle-on-load>`, pages.page) or reached by the
 * redirect after it; a confirmation page, which marks its way back
 * `data-settle` (templates/_confirm.html); or one with a notice, whose
 * words are marked so (templates/_notice.html). As it loads, taps wait a
 * moment; an ignored one focuses what the page marks, else its heading.
 * A link followed to any other page is not a double tap's to land on.
 */
import { ignoreTaps } from "../shared/settling";

/** Whether this page was reached by a redirect, as a form's answer is. */
const redirected = () =>
  performance
    .getEntriesByType("navigation")
    .some(
      (arrival) => (arrival as PerformanceNavigationTiming).redirectCount > 0,
    );

/** Ignores taps for SETTLE_MS if a tap brought this page up. */
export function settleOnLoad(document: Document): void {
  const marked = document.querySelector<HTMLElement>("[data-settle]");
  if (
    marked !== null ||
    document.documentElement.hasAttribute("data-settle-on-load") ||
    redirected()
  ) {
    // The heading, focusable by script (admin_base.html).
    ignoreTaps(document, () => marked ?? document.querySelector("h1"));
  }
}
