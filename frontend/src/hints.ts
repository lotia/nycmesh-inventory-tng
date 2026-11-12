import { decimal, thousandths } from "./quantity";

/**
 * Words that follow a field as it is filled in, each still right without
 * the browser's help.
 *
 * A hint about the package a select picks, shown only while it is one it
 * fits: the printed-length hint under an open package nobody recorded, on
 * the count page, for packages of items in a length (inventory-tng-lby).
 * Without this, the page shows the hint whenever any it offers fits.
 *
 * The hint carries `data-hint-for`, its select's name; the options it fits
 * carry `data-hinted`; the field it describes names it in
 * aria-describedby, which is taken off while it is hidden.
 */
export function followPicks(document: Document): void {
  for (const hint of document.querySelectorAll<HTMLElement>(
    "[data-hint-for]",
  )) {
    const select = document.querySelector<HTMLSelectElement>(
      `select[name="${hint.dataset.hintFor}"]`,
    );
    const field = document.querySelector<HTMLElement>(
      `[aria-describedby~="${hint.id}"]`,
    );
    if (select === null || field === null) {
      continue;
    }
    const show = () => {
      const picked = select.options[select.selectedIndex];
      const fits = picked?.hasAttribute("data-hinted") ?? false;
      hint.hidden = !fits;
      // Only its own id, among any others describing the field.
      const ids = (field.getAttribute("aria-describedby") ?? "")
        .split(" ")
        .filter((id) => id !== "" && id !== hint.id);
      const described = fits ? [...ids, hint.id] : ids;
      if (described.length > 0) {
        field.setAttribute("aria-describedby", described.join(" "));
      } else {
        field.removeAttribute("aria-describedby");
      }
    };
    select.addEventListener("change", show);
    show();
  }
}

/**
 * A button's words that say what is typed in a field: "Set to 40 m" on the
 * length-checks page (inventory-tng-b1z). Without this, the page says
 * "Set to it", true whatever is typed. The words carry `data-follows`,
 * the field's id, and `data-unit`; with no number in the field, they say
 * "it".
 */
export function followTyped(document: Document): void {
  for (const words of document.querySelectorAll<HTMLElement>(
    "[data-follows]",
  )) {
    // The selector found it: the attribute is there.
    const field = document.getElementById(String(words.dataset.follows));
    if (!(field instanceof HTMLInputElement)) {
      continue;
    }
    const unit = words.dataset.unit ? ` ${words.dataset.unit}` : "";
    const say = () => {
      // A number, as the page takes one; else what is typed is no length.
      const typed = field.value.trim();
      const length = typed === "" ? null : thousandths(typed);
      words.textContent = length === null ? "it" : `${decimal(length)}${unit}`;
    };
    field.addEventListener("input", say);
    say();
  }
}
