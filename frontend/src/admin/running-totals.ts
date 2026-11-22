import { decimal, sent, thousandths } from "../shared/quantity";

/**
 * What a place on the count form holds once saved, under "Moved to" it:
 * what is typed on its own lines of an item moved there from a retired
 * area emptied while it was counted, plus how many arrived of each move
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
  for (const line of document.querySelectorAll<HTMLElement>("[data-total]")) {
    const shown = line.querySelector<HTMLElement>("[data-total-amount]");
    const boxes = [
      ...document.querySelectorAll<HTMLInputElement>(
        `input[data-adds-to="${line.dataset.total}"]`,
      ),
    ];
    if (shown === null || boxes.length === 0) {
      continue;
    }
    const states = boxes.map((box) =>
      box.dataset.state
        ? document.querySelector<HTMLSelectElement>(
            `select[name="${box.dataset.state}"]`,
          )
        : null,
    );
    const add = () => {
      let total = 0;
      for (const [index, box] of boxes.entries()) {
        if ((states[index]?.value ?? "here") !== "here") {
          continue;
        }
        const typed = box.value.trim();
        const amount =
          typed === "" ? sent(box.dataset.unset ?? "0") : thousandths(typed);
        if (amount === null) {
          line.hidden = true;
          return;
        }
        // Thousandths times thousandths: back to thousandths.
        total += (amount * sent(box.dataset.per ?? "1")) / 1000;
      }
      shown.textContent = decimal(total);
      line.hidden = false;
    };
    for (const box of boxes) {
      box.addEventListener("input", add);
    }
    for (const state of states) {
      state?.addEventListener("change", add);
    }
    add();
  }
}
