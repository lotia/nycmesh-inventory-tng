import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { focusInPlace, ignoreTaps, SETTLE_MS } from "./settling";

describe("ignoreTaps (Ali, 2026-11-27, inventory-tng-r3jx)", () => {
  let tapped: string[];
  const button = (name: string) => {
    const made = document.createElement("button");
    made.textContent = name;
    made.addEventListener("click", () => tapped.push(name));
    document.body.append(made);
    return made;
  };
  beforeEach(() => {
    tapped = [];
    vi.useFakeTimers({ shouldAdvanceTime: true });
  });
  afterEach(() => {
    document.body.replaceChildren();
  });

  test("ignores every tap on the page for SETTLE_MS, then takes them again", async () => {
    const other = button("other");
    ignoreTaps(document);
    other.dispatchEvent(new MouseEvent("click", { bubbles: true, detail: 1 }));
    expect(tapped).toEqual([]);
    await vi.advanceTimersByTimeAsync(SETTLE_MS - 1);
    other.dispatchEvent(new MouseEvent("click", { bubbles: true, detail: 1 }));
    expect(tapped).toEqual([]);
    await vi.advanceTimersByTimeAsync(1);
    other.dispatchEvent(new MouseEvent("click", { bubbles: true, detail: 1 }));
    expect(tapped).toEqual(["other"]);
  });

  test("an ignored pointer tap focuses the target, without scrolling; a key's press leaves focus", () => {
    const target = button("target");
    const other = button("other");
    const focus = vi.spyOn(HTMLElement.prototype, "focus");
    ignoreTaps(document, () => target);
    other.focus();
    other.dispatchEvent(new MouseEvent("click", { bubbles: true, detail: 0 }));
    expect(document.activeElement).toBe(other);
    other.dispatchEvent(new MouseEvent("click", { bubbles: true, detail: 1 }));
    expect(document.activeElement).toBe(target);
    expect(focus).toHaveBeenLastCalledWith({ preventScroll: true });
    expect(tapped).toEqual([]);
    focus.mockRestore();
  });

  test("a pressed box or list takes no focus meanwhile", async () => {
    const box = document.createElement("input");
    document.body.append(box);
    ignoreTaps(document);
    const down = new MouseEvent("mousedown", {
      bubbles: true,
      cancelable: true,
    });
    box.dispatchEvent(down);
    expect(down.defaultPrevented).toBe(true);
    await vi.advanceTimersByTimeAsync(SETTLE_MS);
    const later = new MouseEvent("mousedown", {
      bubbles: true,
      cancelable: true,
    });
    box.dispatchEvent(later);
    expect(later.defaultPrevented).toBe(false);
  });

  test("called again, the wait starts again, with the new target", async () => {
    const first = button("first");
    const second = button("second");
    ignoreTaps(document, () => first);
    await vi.advanceTimersByTimeAsync(SETTLE_MS - 100);
    ignoreTaps(document, () => second);
    await vi.advanceTimersByTimeAsync(SETTLE_MS - 1);
    first.dispatchEvent(new MouseEvent("click", { bubbles: true, detail: 1 }));
    expect(tapped).toEqual([]);
    expect(document.activeElement).toBe(second);
  });
});

test("focusInPlace focuses without scrolling, and does nothing with none", () => {
  const box = document.createElement("input");
  document.body.append(box);
  const focus = vi.spyOn(box, "focus");
  focusInPlace(box);
  expect(focus).toHaveBeenCalledWith({ preventScroll: true });
  expect(() => focusInPlace(null)).not.toThrow();
  box.remove();
});
