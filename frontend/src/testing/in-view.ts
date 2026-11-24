/** What is in view, as the tests say: IntersectionObserver stubbed so a
 * test tells each one watching how much of its element shows. Never
 * bundled. */
import { act } from "@testing-library/preact";
import { vi } from "vitest";

type Watcher = {
  callback: IntersectionObserverCallback;
  watched: Element[];
};

/** Stubs IntersectionObserver; vi.unstubAllGlobals undoes it. Returns
 * how to say how much of an element is on the screen, and what is
 * watched. */
export function stubInView() {
  const watchers = new Set<Watcher>();
  class Stub {
    readonly watcher: Watcher;
    constructor(callback: IntersectionObserverCallback) {
      this.watcher = { callback, watched: [] };
      watchers.add(this.watcher);
    }
    observe(element: Element) {
      this.watcher.watched.push(element);
    }
    disconnect() {
      watchers.delete(this.watcher);
    }
  }
  vi.stubGlobal("IntersectionObserver", Stub);
  return {
    /** Tells those watching `element` that `ratio` of it shows. */
    inView(element: Element, ratio: number) {
      act(() => {
        for (const { callback, watched } of watchers) {
          if (watched.includes(element)) {
            const entry = { target: element, intersectionRatio: ratio };
            callback(
              [entry as IntersectionObserverEntry],
              {} as IntersectionObserver,
            );
          }
        }
      });
    },
    /** How many elements are watched now. */
    watched: () =>
      [...watchers].reduce((sum, each) => sum + each.watched.length, 0),
  };
}
