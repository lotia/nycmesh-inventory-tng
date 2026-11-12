/**
 * Which build of the pages this is, and whether a newer one is out
 * (backend page_builds.py; docs/decisions/0010, 2026-11-12).
 *
 * A build's id is when it was made, in UTC (vite.config.ts), so a newer
 * build's sorts after. Each build keeps the outbox and the cart under keys
 * of its own and reads no other's, so a tab of an older build, open during a
 * deploy, sends what it holds itself. It learns that a newer build is out
 * from another tab's stamp in storage, or from the server: its reply to a
 * batch names the server's build, and a batch from a build too old is held
 * back (409 build_too_old). The scan page then asks for a reload, once
 * nothing waits to send and the cart is saved.
 */
import { signal } from "@preact/signals";
import { onStoredChange, writeStored } from "./stored";
import { telemetry } from "./telemetry";

declare const __BUILD__: string;

/** This build's id (vite.config.ts). */
export const BUILD: string = __BUILD__;
/** Where each tab stamps its build's id as it starts: a key no build
 * changes, so a tab hears of a newer build whatever keys that build keeps. */
export const BUILD_KEY = "inventory.build";
/** The header a batch names its build in, and the reply the server's. */
export const HEADER = "X-Inventory-Build";
/** As the backend checks one (bundle.BUILD_ID). */
const ID = /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$/;

/** How a tab learned that a newer build is out: from another tab's stamp,
 * from the server's reply, or from a batch held back as too old. */
export type Learned = "other_tab" | "server" | "too_old";

/** The build a stored value was stamped with, if any. */
export function stampOf(stored: unknown): unknown {
  return typeof stored === "object" && stored !== null && "build" in stored
    ? stored.build
    : undefined;
}

export class Builds {
  /** How this tab learned that a newer build is out; null until it does.
   * Too old, once known, stays: nothing this tab sends will be taken. */
  readonly newer = signal<Learned | null>(null);

  constructor(
    readonly build = BUILD,
    private readonly storage: Storage = localStorage,
  ) {}

  /** Whether `other` names a build newer than this one. */
  isNewer(other: unknown): boolean {
    return typeof other === "string" && ID.test(other) && other > this.build;
  }

  /** A newer build is out, learned so. */
  learn(how: Learned): void {
    const now = this.newer.value;
    // The first way, then too old if learned later, which says more.
    if (now !== null && (how !== "too_old" || now === "too_old")) {
      return;
    }
    this.newer.value = how;
    telemetry.record({ kind: "out_of_date", learned: how });
  }

  /** Learn from `other`, a build another tab or the server named. */
  heard(other: unknown, how: "other_tab" | "server"): void {
    if (this.isNewer(other)) {
      this.learn(how);
    }
  }

  /** Stamp this build in BUILD_KEY, and follow other tabs' stamps there
   * and on `keys`, which hold what pages keep. Returns what stops
   * following. A newer stamp found there is not learned from: this page
   * was just loaded, so it is what the server serves, and the stamp may
   * be left by a build since rolled back, or one built by a clock ahead,
   * whose banner a reload would never clear. */
  listen(keys: readonly string[]): () => void {
    writeStored(this.storage, BUILD_KEY, { build: this.build });
    return onStoredChange([BUILD_KEY, ...keys], (event) => {
      let stored: unknown;
      try {
        stored = JSON.parse(event.newValue ?? "null");
      } catch {
        return;
      }
      this.heard(stampOf(stored), "other_tab");
    });
  }
}
