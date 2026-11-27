/**
 * What a page shows from its fields' values, kept current: words after a
 * box, a hint, a total, a link. Every script of the administrator pages
 * that fills words or state from a field goes through `follow`
 * (backend/tests/codebase/test_follow_fields.py).
 */

/** A field whose value something on the page shows. */
export type Field = HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement;

/**
 * Runs `show` now, as any of `fields` is changed (input for a box or a
 * text area, change for a list), and on the window's pageshow. A value the browser puts back
 * by itself, on Back or in a restored tab, fires neither, and Chromium puts
 * it back after the page's script has run (inventory-tng-r86h).
 */
export function follow(
  document: Document,
  fields: Iterable<Field>,
  show: () => void,
): void {
  for (const field of fields) {
    field.addEventListener(
      field instanceof HTMLSelectElement ? "change" : "input",
      show,
    );
  }
  document.defaultView?.addEventListener("pageshow", show);
  show();
}
