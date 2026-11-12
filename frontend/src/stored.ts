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

/** What older builds kept under `key`'s older versions, oldest first: a
 * key ending in `.v3` has `.v1` and `.v2`, at 0 and 1, each undefined if
 * nothing readable is there. Read, never written nor removed: an older
 * build's tab may still be using them, and what they hold is reported, not
 * read (build.ts). */
export function olderVersions(storage: Storage, key: string): unknown[] {
  const [, name, version] = /^(.*\.v)(\d+)$/.exec(key) ?? [];
  return Array.from({ length: Math.max(Number(version ?? 1) - 1, 0) }, (_, n) =>
    readStored(storage, `${name}${n + 1}`),
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
