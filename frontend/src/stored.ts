/**
 * Reading and writing what the pages keep in localStorage. A browser may
 * refuse either (private browsing, a full disk, storage turned off), so
 * neither throws: callers decide what a refusal means.
 */

/** What is stored under `key`, parsed; undefined if nothing readable is. */
export function readStored(storage: Storage, key: string): unknown {
  try {
    return parseStored(storage.getItem(key));
  } catch {
    return undefined;
  }
}

/** Stored text, parsed; undefined if there is none, or it is not JSON. */
export function parseStored(text: string | null): unknown {
  try {
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

/** Whether anything is stored under `key`, readable or not; false if the
 * browser will not say. */
export function hasStored(storage: Storage, key: string): boolean {
  try {
    return storage.getItem(key) !== null;
  } catch {
    return false;
  }
}

/** False if the browser would not forget it. */
export function removeStored(storage: Storage, key: string): boolean {
  try {
    storage.removeItem(key);
    return true;
  } catch {
    return false;
  }
}

/** `key`'s older versions, oldest first: a key ending in `.v3` has `.v1`
 * and `.v2`; one with no version has none. Where older builds kept what
 * this build lists to put back or discard (older.ts). */
export function olderKeys(key: string): string[] {
  const [, name, version] = /^(.*\.v)(\d+)$/.exec(key) ?? [];
  return Array.from(
    { length: Math.max(Number(version ?? 1) - 1, 0) },
    (_, n) => `${name}${n + 1}`,
  );
}

/** Call `changed` when another tab writes one of `keys`. Returns what
 * stops it. */
export function onStoredChange(
  keys: readonly string[],
  changed: (event: StorageEvent) => void,
): () => void {
  const listener = (event: StorageEvent) => {
    if (event.key !== null && keys.includes(event.key)) {
      changed(event);
    }
  };
  window.addEventListener("storage", listener);
  return () => window.removeEventListener("storage", listener);
}
