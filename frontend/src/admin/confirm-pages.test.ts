import { afterEach, describe, expect, it, vi } from "vitest";
import { SETTLE_MS, stopIgnoringTaps } from "../shared/settling";
import { settleConfirmPage } from "./confirm-pages";

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

const tap = (on: HTMLElement, detail = 1) =>
  on.dispatchEvent(
    new MouseEvent("click", { bubbles: true, cancelable: true, detail }),
  );

afterEach(() => {
  stopIgnoringTaps(document);
  vi.useRealTimers();
  document.body.replaceChildren();
});

describe("a confirmation page (Ali, 2026-11-27, inventory-tng-0c2z)", () => {
  it("ignores taps a moment as it loads, an ignored one focusing the way to keep things", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const revoke = page(
      `<form><div class="actions"><button class="danger">Revoke</button><a href="/back" data-keep>Keep it</a></div></form>`,
    );
    settleConfirmPage(document);
    // The second tap of the double tap that brought the page up.
    tap(revoke);
    expect(tapped).toEqual([]);
    expect(document.activeElement).toBe(document.querySelector("[data-keep]"));
    await vi.advanceTimersByTimeAsync(SETTLE_MS);
    tap(revoke);
    expect(tapped).toEqual(["Revoke"]);
  });

  it("leaves another page's taps alone", () => {
    const save = page(`<form><button>Save</button></form>`);
    settleConfirmPage(document);
    tap(save);
    expect(tapped).toEqual(["Save"]);
  });
});
