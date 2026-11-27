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
 * text area, change for a list), and on the window's pageshow if any
 * holds another value than when it last ran. A value the browser puts
 * back by itself, on Back or in a restored tab, fires neither input nor
 * change, and Chromium puts it back after the page's script has run
 * (inventory-tng-r86h). Pageshow fires on every load too, when nothing
 * has changed: `show` is not run again then, so words in a live region
 * are not read out again.
 */
export function follow(
  document: Document,
  fields: Iterable<Field>,
  show: () => void,
): void {
  const followed = [...fields];
  const values = () => followed.map(held);
  let shown: string[] = [];
  const update = () => {
    shown = values();
    show();
  };
  for (const field of followed) {
    field.addEventListener(
      field instanceof HTMLSelectElement ? "change" : "input",
      update,
    );
  }
  document.defaultView?.addEventListener("pageshow", () => {
    if (values().some((value, at) => value !== shown[at])) {
      update();
    }
  });
  update();
}

/** What `field` holds: a box's or a list's value, or whether a checkbox or
 * radio is ticked, which its value does not say. */
function held(field: Field): string {
  return field instanceof HTMLInputElement &&
    (field.type === "checkbox" || field.type === "radio")
    ? String(field.checked)
    : field.value;
}
