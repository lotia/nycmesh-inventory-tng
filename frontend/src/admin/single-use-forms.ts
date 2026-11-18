/**
 * A form that makes something shown once (backend web/single_use_forms.py) is
 * sent once: a double-click would show only the second answer, "Already
 * sent". The flag stops it; disabling the buttons shows it, once the form's
 * data is taken, so a named button still sends its value. Back to a kept
 * page, its spent key is useless, so the page is fetched again.
 */

/** The field such a form carries its key in (backend single_use_forms.FIELD). */
export const SINGLE_USE_FIELD = "single_use";

export function sendOnce(window: Window): void {
  // On the window, after the document's listeners, such as the retire
  // guard's, which may stop the form being sent.
  window.addEventListener("submit", (event) => {
    const form = event.target;
    if (
      event.defaultPrevented ||
      !(form instanceof HTMLFormElement) ||
      !form.querySelector(`input[name="${SINGLE_USE_FIELD}"]`)
    ) {
      return;
    }
    if (form.dataset.sent) {
      event.preventDefault();
      return;
    }
    form.dataset.sent = "yes";
    window.setTimeout(() => {
      for (const control of form.elements) {
        if (
          (control instanceof HTMLButtonElement ||
            control instanceof HTMLInputElement) &&
          control.type === "submit"
        ) {
          control.disabled = true;
        }
      }
    });
  });
  window.addEventListener("pageshow", (event) => {
    if (event.persisted && window.document.querySelector("form[data-sent]")) {
      window.location.reload();
    }
  });
}
