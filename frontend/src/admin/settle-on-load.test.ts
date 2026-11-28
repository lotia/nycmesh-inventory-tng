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
  vi.restoreAllMocks();
  document.documentElement.removeAttribute("data-settle-on-load");
  document.body.replaceChildren();
});

/** A form's answer, as it comes: sent back by its POST, or by a redirect. */
const answers: [string, () => void][] = [
  [
    "sent back by its POST",
    () => document.documentElement.setAttribute("data-settle-on-load", ""),
  ],
  [
    "reached by the redirect after it",
    () => {
      vi.spyOn(performance, "getEntriesByType").mockReturnValue([
        { redirectCount: 1 } as PerformanceNavigationTiming,
      ]);
    },
  ],
];

describe("a page a tap brought up (Ali, 2026-11-27, inventory-tng-0c2z)", () => {
  it.each([
    [
      "a confirmation page",
      `<form><div class="actions"><button class="danger">Revoke</button><a href="/back" data-settle>Keep it</a></div></form>`,
      "Revoke",
    ],
    [
      "a notice",
      `<p role="status" data-settle tabindex="-1">Retired: it is no longer offered.</p><form><button>Retire</button></form>`,
      "Retire",
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

  it.each(answers)(
    "a form's answer %s ignores taps a moment, an ignored one focusing its heading",
    async (_, arrive) => {
      vi.useFakeTimers({ shouldAdvanceTime: true });
      const button = page(
        `<h1 tabindex="-1">Recorded more than moved</h1><form><button>Bring back Store</button></form>`,
      );
      arrive();
      settleOnLoad(document);
      tap(button);
      expect(tapped).toEqual([]);
      expect(document.activeElement).toBe(document.querySelector("h1"));
      await vi.advanceTimersByTimeAsync(SETTLE_MS);
      tap(button);
      expect(tapped).toEqual(["Bring back Store"]);
    },
  );

  it("leaves another page's taps alone", () => {
    const save = page(`<form><button>Save</button></form>`);
    settleOnLoad(document);
    tap(save);
    expect(tapped).toEqual(["Save"]);
  });

  it("leaves a page a link was followed to alone, reloaded or not", () => {
    for (const type of ["navigate", "reload", "back_forward"]) {
      vi.spyOn(performance, "getEntriesByType").mockReturnValue([
        { redirectCount: 0, type } as PerformanceNavigationTiming,
      ]);
      const save = page(`<form><button>Save</button></form>`);
      settleOnLoad(document);
      tap(save);
      expect(tapped).toEqual(["Save"]);
    }
  });
});
