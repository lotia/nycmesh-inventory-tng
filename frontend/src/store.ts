/**
 * The half of `useSyncExternalStore` that is the same every time.
 *
 * React asks a module for two things: a way to subscribe that hands back a way
 * to unsubscribe, and a snapshot it may compare by identity. The second is
 * always the module's own -- a boolean here, a memoised array there -- and the
 * first is always this, so it was written twice before it was written once.
 *
 * WHAT THE CONTRACT ACTUALLY REQUIRES, since a caller has to keep it and this
 * cannot: `subscribe` must be stable across renders, which it is because it is
 * a module binding; and a snapshot must not be a fresh object per read, or
 * React re-renders without end. `batch/outbox.ts` says the same thing about
 * its own held array.
 */

export type Notifier = {
  /** Subscribe, and hand back the way to stop. */
  subscribe: (watcher: () => void) => () => void;
  /** Tell everyone the snapshot has moved. Call it AFTER the change. */
  changed: () => void;
};

export function notifier(): Notifier {
  const watching = new Set<() => void>();
  return {
    subscribe(watcher: () => void): () => void {
      watching.add(watcher);
      return () => {
        watching.delete(watcher);
      };
    },
    changed(): void {
      for (const watcher of watching) {
        watcher();
      }
    },
  };
}
