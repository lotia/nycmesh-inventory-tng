import { boxAmount, readBox } from "../shared/quantity";

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
      showDescribing(field, hint, picked?.hasAttribute("data-hinted") ?? false);
    };
    select.addEventListener("change", show);
    show();
  }
}

/**
 * Shows or hides words describing a field, naming them last in its
 * aria-describedby only while shown: an element it names is read even
 * when hidden. Other ids describing the field are kept.
 */
function showDescribing(field: Element, words: HTMLElement, shown: boolean) {
  words.hidden = !shown;
  const ids = (field.getAttribute("aria-describedby") ?? "")
    .split(" ")
    .filter((id) => id !== "" && id !== words.id);
  const described = shown ? [...ids, words.id] : ids;
  if (described.length > 0) {
    field.setAttribute("aria-describedby", described.join(" "));
  } else {
    field.removeAttribute("aria-describedby");
  }
}

/**
 * The unit of the item a select picks, right after the box its amount is
 * typed in: "m" beside How much on the adjustment form, once a cable in m
 * is picked (inventory-tng-mdvz). The words carry `data-unit-of`, the
 * select's name; each option, `data-unit`. Without this, the unit shows
 * only in the option's words, as "Cable (m)".
 */
export function followUnits(document: Document): void {
  for (const words of document.querySelectorAll<HTMLElement>(
    "[data-unit-of]",
  )) {
    const select = document.querySelector<HTMLSelectElement>(
      `select[name="${words.dataset.unitOf}"]`,
    );
    if (select === null) {
      continue;
    }
    const show = () => {
      words.textContent =
        select.options[select.selectedIndex]?.dataset.unit ?? "";
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
 * what the page sent, "it" in the page's language.
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
    const sent = words.textContent;
    const say = () => {
      // A number, as the page takes one, written back as its boxes are
      // (boxAmount), in its decimal separator; else what is typed is no
      // length.
      const typed = field.value.trim();
      const length = typed === "" ? null : readBox(typed);
      words.textContent =
        length === null ? sent : `${boxAmount(length)}${unit}`;
    };
    field.addEventListener("input", say);
    say();
  }
}

/**
 * What a field held when the page was sent, shown under it once it is
 * changed: "Was 305 m" under an open package's count on the recorded-more
 * page (inventory-tng-0gg). The field carries `data-was`, what it held; the
 * words are the element with its id and `-was`, named in its
 * aria-describedby only while shown (inventory-tng-suqp). Without this,
 * they show only on a page sent back with another count.
 *
 * Checked at once and on pageshow too, not only as it is typed in: a
 * count the browser puts back by itself, on Back or in a restored tab,
 * fires no input (inventory-tng-2t5h).
 */
export function followWas(document: Document): void {
  for (const field of document.querySelectorAll<HTMLInputElement>(
    "input[data-was]",
  )) {
    const was = document.getElementById(`${field.id}-was`);
    if (was === null) {
      continue;
    }
    const show = () => {
      showDescribing(field, was, field.value !== field.dataset.was);
    };
    field.addEventListener("input", show);
    document.defaultView?.addEventListener("pageshow", show);
    show();
  }
}
