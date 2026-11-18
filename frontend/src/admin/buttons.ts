/**
 * Buttons of the administrator pages that need the browser: each is hidden
 * until shown here, so a page without its script offers none.
 */

/** `data-print`: prints the page (styles.css, Printing). */
export function printButtons(document: Document): void {
  for (const button of document.querySelectorAll<HTMLButtonElement>(
    "button[data-print]",
  )) {
    button.addEventListener("click", () => document.defaultView?.print());
    button.hidden = false;
  }
}

/**
 * `data-copy`: copies the text of the element whose id it names, and says
 * so in the one `data-status` names, in the words of `data-copied` or
 * `data-failed`, which the server translates. Shown only where the browser
 * has a clipboard: what it copies can always be copied by hand.
 */
export function copyButtons(document: Document): void {
  const clipboard = document.defaultView?.navigator.clipboard;
  if (!clipboard) return;
  for (const button of document.querySelectorAll<HTMLButtonElement>(
    "button[data-copy]",
  )) {
    const { copy = "", status = "", copied = "", failed = "" } = button.dataset;
    const say = (words: string) => {
      const said = document.getElementById(status);
      if (said) said.textContent = words;
    };
    button.addEventListener("click", () => {
      // Emptied first, so the same words are said again.
      say("");
      const text = document.getElementById(copy)?.textContent ?? "";
      clipboard.writeText(text).then(
        () => say(copied),
        () => say(failed),
      );
    });
    button.hidden = false;
  }
}
