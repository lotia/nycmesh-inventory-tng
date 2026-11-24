import { count, decimal, readBox, sent } from "../shared/quantity";

/**
 * What a place on the count form holds once saved, under "Moved to" it:
 * what is typed on its own lines of an item moved there from a retired
 * area emptied while it was counted, plus how many arrived of each move,
 * a box for each form it arrived in, sealed packages whole
 * (inventory-tng-ygk5). Without this, the line stays hidden, as the
 * page sends it.
 *
 * The line carries `data-total`, its key, and the number in it
 * `data-total-amount`; each box it adds up carries `data-adds-to`, that
 * key, `data-unset`, what a blank box counts as (what the records say,
 * or the amount moved), and `data-per`, how many units one typed is (a
 * sealed package's size), if not one. An open package's box carries
 * `data-state`, the name of the select that may close it, which counts
 * it as none. A box that holds no number hides the line until it does.
 * The words are a polite live region, so a new total is read out; it
 * changes on input, as one types, and reading out coalesces.
 */
export function followTotals(document: Document): void {
  const boxesOf = new Map<string | undefined, HTMLInputElement[]>();
  for (const box of document.querySelectorAll<HTMLInputElement>(
    "input[data-adds-to]",
  )) {
    const key = box.dataset.addsTo;
    boxesOf.set(key, [...(boxesOf.get(key) ?? []), box]);
  }
  for (const line of document.querySelectorAll<HTMLElement>("[data-total]")) {
    const shown = line.querySelector<HTMLElement>("[data-total-amount]");
    const boxes = (boxesOf.get(line.dataset.total) ?? []).map((box) => ({
      box,
      state: box.dataset.state
        ? document.querySelector<HTMLSelectElement>(
            `select[name="${box.dataset.state}"]`,
          )
        : null,
    }));
    if (shown === null || boxes.length === 0) {
      continue;
    }
    const add = () => {
      let total = 0;
      for (const { box, state } of boxes) {
        if ((state?.value ?? "here") !== "here") {
          continue;
        }
        const amount = boxAmount(box);
        if (amount === null) {
          line.hidden = true;
          return;
        }
        // Thousandths times thousandths: back to thousandths.
        total += Math.round((amount * sent(box.dataset.per ?? "1")) / 1000);
      }
      shown.textContent = decimal(total);
      line.hidden = false;
    };
    for (const { box, state } of boxes) {
      box.addEventListener("input", add);
      state?.addEventListener("change", add);
    }
    add();
  }
}

/** What a box adds, in thousandths of what it counts (a sealed package,
 * whole, as the server reads it), read in the page's decimal separator
 * (readBox), what it was sent as if blank; null if it holds no such
 * number. */
function boxAmount(box: HTMLInputElement): number | null {
  const typed = box.value.trim();
  if (typed === "") {
    return sent(box.dataset.unset ?? "0");
  }
  if (box.dataset.per === undefined) {
    return readBox(typed);
  }
  const packages = count(typed);
  return packages === null ? null : packages * 1000;
}
