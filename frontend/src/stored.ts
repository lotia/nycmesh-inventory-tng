/**
 * Reading and writing what the pages keep in localStorage. A browser may
 * refuse either (private browsing, a full disk, storage turned off), so
 * neither throws: callers decide what a refusal means.
 */

/** What is stored under `key`, parsed; undefined if nothing readable is. */
export function readStored(storage: Storage, key: string): unknown {
  try {
    const text = storage.getItem(key);
    return text === null ? undefined : JSON.parse(text);
  } catch {
    return undefined;
  }
}

/** False if the browser would not keep it. */
export function writeStored(
  storage: Storage,
  key: string,
  value: unknown,
): boolean {
  try {
    storage.setItem(key, JSON.stringify(value));
    return true;
  } catch {
    return false;
  }
}

/** Call `changed` when another tab writes `key`. Returns what stops it. */
export function onStoredChange(key: string, changed: () => void): () => void {
  const listener = (event: StorageEvent) => {
    if (event.key === key) {
      changed();
    }
  };
  window.addEventListener("storage", listener);
  return () => window.removeEventListener("storage", listener);
}
