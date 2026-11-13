/** Fake storage for the tests. Never bundled. */

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
