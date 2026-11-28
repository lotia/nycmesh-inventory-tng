/**
 * Retiring is one tap, as it can be undone, but a page with changes not
 * yet saved warns first (docs/decisions/0019): retire anyway, losing them,
 * or go back. A retire form says it is one with `data-retire`.
 */
import { msg } from "@lingui/core/macro";
import { say } from "../shared/i18n";
import { focusAndIgnoreTaps, ignoreTaps } from "../shared/settling";

export const UNSAVED_CHANGES = msg`This page has changes you haven't saved. Retiring now loses them.`;
export const GO_BACK = msg`Go back`;
export const RETIRE_ANYWAY = msg`Retire anyway`;

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
  const words = document.createElement("p");
  words.id = "retire-question";
  words.className = "mt-0";
  words.textContent = say(UNSAVED_CHANGES);
  const form = document.createElement("form");
  form.method = "dialog";
  form.className = "actions";
  const back = button(document, "back", say(GO_BACK));
  back.className = "primary";
  back.autofocus = true;
  form.append(back, button(document, "retire", say(RETIRE_ANYWAY)));
  dialog.append(words, form);
  document.body.append(dialog);
  return dialog;
}

/** A button closing the dialog with `value`, saying `words`. */
function button(
  document: Document,
  value: string,
  words: string,
): HTMLButtonElement {
  const made = document.createElement("button");
  made.value = value;
  made.textContent = words;
  return made;
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
    const retire = () => form.querySelector("button");
    const dialog = question(document);
    dialog.addEventListener("close", () => {
      dialog.remove();
      // The page under the question comes under the finger: taps wait a
      // moment, and focus goes back to Retire, without scrolling (Ali,
      // 2026-11-27, inventory-tng-r3jx).
      focusAndIgnoreTaps(document, retire);
      if (dialog.returnValue === "retire") {
        form.dataset.sure = "yes";
        form.requestSubmit();
      }
    });
    dialog.showModal();
    // The question comes under the finger: taps wait a moment, and an
    // ignored one focuses Go back, the safe choice (Ali, 2026-11-27,
    // inventory-tng-0c2z).
    ignoreTaps(document, () => dialog.querySelector("button"));
  });
}
