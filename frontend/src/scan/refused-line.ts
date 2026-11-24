/**
 * The cart line a refusal is about: a status line's refusal is said till
 * that line changes, such as its loose amount edited, here or in another
 * tab, and not on other changes to the cart; words saying what was added
 * stay (Ali, 2026-11-24, inventory-tng-2l6n).
 */
import { useLayoutEffect, useRef } from "preact/hooks";
import type { Cart } from "./cart";

/** The item a refusal is about, and its cart line as it was then. */
export type About = { itemId: number; line: string };

/** The line for `itemId` as `cart` has it now, none included. */
export function aboutLine(cart: Cart, itemId: number): About {
  return { itemId, line: lineNow(cart, itemId) };
}

/** Whether the line `about` names has changed in `cart` since. */
export function lineChanged(about: About | undefined, cart: Cart): boolean {
  return about !== undefined && lineNow(cart, about.itemId) !== about.line;
}

/** Calls `clear` once the line a refusal said is about changes in `cart`,
 * before the page is painted. Returns what to call with each new thing
 * said: the line it is about, if a refusal, else nothing. */
export function useClearedWhenLineChanges(
  cart: Cart,
  clear: () => void,
): (about?: About) => void {
  const said = useRef<About | undefined>(undefined);
  useLayoutEffect(() => {
    if (lineChanged(said.current, cart)) {
      said.current = undefined;
      clear();
    }
  }, [cart]);
  return (about) => {
    said.current = about;
  };
}

/** The line's fields in one order, so a line read back from storage
 * compares equal to the same line built here. */
function lineNow(cart: Cart, itemId: number): string {
  const line = cart.lines.find((each) => each.itemId === itemId) ?? null;
  return JSON.stringify(line, (_key, value: unknown) =>
    value !== null && typeof value === "object" && !Array.isArray(value)
      ? Object.fromEntries(
          Object.entries(value).sort(([a], [b]) => (a < b ? -1 : 1)),
        )
      : value,
  );
}
