import { afterEach, describe, expect, it, vi } from "vitest";
import { SETTLE_MS, stopIgnoringTaps } from "../shared/settling";
import { settleOnLoad } from "./settle-on-load";

/** Each button tapped, by its words. */
let tapped: string[] = [];

function page(html: string) {
  tapped = [];
  document.body.innerHTML = html;
  for (const button of document.querySelectorAll("button")) {
    button.addEventListener("click", (event) => {
      event.preventDefault();
      tapped.push(button.textContent ?? "");
    });
  }
  return document.querySelector("button") as HTMLButtonElement;
}

const tap = (on: HTMLElement) =>
  on.dispatchEvent(
    new MouseEvent("click", { bubbles: true, cancelable: true, detail: 1 }),
  );

afterEach(() => {
  stopIgnoringTaps(document);
  vi.useRealTimers();
  document.body.replaceChildren();
});

describe("a page a tap brought up (Ali, 2026-11-27, inventory-tng-0c2z)", () => {
  it.each([
    [
      "a confirmation page",
      `<form><div class="actions"><button class="danger">Revoke</button><a href="/back" data-settle>Keep it</a></div></form>`,
      "Revoke",
    ],
    [
      "a notice that offers an undo",
      `<div role="status"><span data-settle tabindex="-1">Retired.</span><form><button>Bring it back</button></form></div>`,
      "Bring it back",
    ],
  ])(
    "%s ignores taps a moment as it loads, an ignored one focusing what it marks",
    async (_, html, words) => {
      vi.useFakeTimers({ shouldAdvanceTime: true });
      const button = page(html);
      settleOnLoad(document);
      // The second tap of the double tap that brought the page up.
      tap(button);
      expect(tapped).toEqual([]);
      expect(document.activeElement).toBe(
        document.querySelector("[data-settle]"),
      );
      await vi.advanceTimersByTimeAsync(SETTLE_MS);
      tap(button);
      expect(tapped).toEqual([words]);
    },
  );

  it("leaves another page's taps alone", () => {
    const save = page(`<form><button>Save</button></form>`);
    settleOnLoad(document);
    tap(save);
    expect(tapped).toEqual(["Save"]);
  });
});
