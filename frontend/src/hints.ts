/**
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
