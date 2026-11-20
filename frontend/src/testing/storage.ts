/** Fake storage for the tests. Never bundled. */
import { onTestFinished, vi } from "vitest";

/** localStorage, but refusing each of `refused`, as a browser may: a full
 * disk refuses setItem, blocked site data refuses everything. */
export function refusing(
  ...refused: ("getItem" | "setItem" | "removeItem")[]
): Storage {
  const storage: Pick<Storage, "getItem" | "setItem" | "removeItem"> = {
    getItem: (key) => localStorage.getItem(key),
    setItem: (key, value) => localStorage.setItem(key, value),
    removeItem: (key) => localStorage.removeItem(key),
  };
  for (const method of refused) {
    storage[method] = () => {
      throw new DOMException(
        "refused",
        method === "setItem" ? "QuotaExceededError" : "SecurityError",
      );
    };
  }
  return storage as Storage;
}

/** The page's own localStorage refusing writes, as a full disk does: to
 * `key` only, or to every key if none is given, until the spy returned is
 * restored, or the test ends, pass or fail, so later tests can write. For
 * code that takes no storage of its own; refusing() is for code that does. */
export function refusingWrites(key?: string) {
  const real = localStorage.setItem.bind(localStorage);
  const setItem = vi
    .spyOn(localStorage, "setItem")
    .mockImplementation((at, value) => {
      if (key === undefined || at === key) {
        throw new DOMException("full", "QuotaExceededError");
      }
      real(at, value);
    });
  onTestFinished(() => setItem.mockRestore());
  return setItem;
}
