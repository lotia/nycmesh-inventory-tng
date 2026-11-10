/**
 * Retiring is one tap, as it can be undone, but a page with changes not
 * yet saved warns first (docs/decisions/0019): retire anyway, losing them,
 * or go back. A retire form says it is one with `data-retire`.
 */

/** Whether a form's fields differ from what the page was sent with. */
export function unsaved(form: HTMLFormElement): boolean {
  for (const field of form.elements) {
    if (field instanceof HTMLSelectElement) {
      // As sent: the last option marked selected, else the first (a page's
      // lists pick one at a time).
      const options = [...field.options];
      const sent =
        options.findLast((option) => option.hasAttribute("selected")) ??
        options[0];
      if (sent !== undefined && !sent.selected) return true;
    } else if (field instanceof HTMLTextAreaElement) {
      if (field.value !== field.defaultValue) return true;
    } else if (field instanceof HTMLInputElement) {
      if (field.type === "checkbox" || field.type === "radio") {
        if (field.checked !== field.defaultChecked) return true;
      } else if (
        field.type !== "hidden" &&
        field.value !== field.defaultValue
      ) {
        return true;
      }
    }
  }
  return false;
}

/** The question, as a dialog: closed with "retire" or "back". */
function question(document: Document): HTMLDialogElement {
  const dialog = document.createElement("dialog");
  dialog.className =
    "m-auto max-w-sm rounded border border-line bg-page p-4 text-ink backdrop:bg-black/50";
  dialog.setAttribute("aria-labelledby", "retire-question");
  dialog.innerHTML = `
    <p id="retire-question" class="mt-0">This page has changes you haven't
    saved. Retiring now loses them.</p>
    <form method="dialog" class="actions">
      <button value="back" class="primary" autofocus>Go back</button>
      <button value="retire">Retire anyway</button>
    </form>`;
  document.body.append(dialog);
  return dialog;
}

/** Asks before a retire form is sent while another form on the page has
 * changes; sends it if told to retire anyway. */
export function guardRetire(document: Document): void {
  document.addEventListener("submit", (event) => {
    const form = event.target;
    if (
      !(form instanceof HTMLFormElement) ||
      !form.hasAttribute("data-retire")
    ) {
      return;
    }
    if (form.dataset.sure === "yes") return;
    const others = [...document.forms].filter((other) => other !== form);
    if (!others.some(unsaved)) return;
    event.preventDefault();
    const dialog = question(document);
    dialog.addEventListener("close", () => {
      dialog.remove();
      if (dialog.returnValue === "retire") {
        form.dataset.sure = "yes";
        form.requestSubmit();
      }
    });
    dialog.showModal();
  });
}
