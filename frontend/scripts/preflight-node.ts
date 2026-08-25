/**
 * Say, in one line, when the node in hand is not the node pinned.
 *
 * `vite.config.ts` calls this as it loads, so every frontend command gets it:
 * the test run, the dev server, a build. It is a note and never a refusal --
 * `mise.toml` pins a version so that a run here and a run in CI are the same
 * run, not because the others are forbidden, and the suite is proven green on
 * the pinned version and on the one that used to break it.
 *
 * What it buys is the sentence that was missing. A node whose globals differ
 * from the pinned one's can fail this suite in hundreds of places at once and
 * name nothing -- `src/test-setup.ts` is one such failure, in full -- and a
 * reader who has been told which node they are on has somewhere to start.
 *
 * The pin is read from `mise.toml` rather than repeated here, because a second
 * copy of a version number is a second thing to forget.
 *
 * Called from the config rather than run as an `npm` lifecycle script, which
 * is where it started. `node scripts/preflight-node.ts` needs node's unflagged
 * type-stripping, so on an older node than this file exists to warn about, a
 * `pretest` spelling of it refused the test run outright -- a note turning
 * into a refusal for exactly the person it was written for. Vite transpiles
 * its own config, so there is no floor here at all.
 */
import { existsSync, readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";

/**
 * The nearest `mise.toml` at or above `from`, or null if there is none.
 *
 * Walked from the working directory rather than from this file's own location:
 * under vitest a module's `import.meta.url` is an http URL served by Vite
 * rather than a path on disk, and a preflight that cannot be imported by its
 * own tests is a preflight nothing checks.
 */
export function findMiseToml(from: string): string | null {
  let dir = resolve(from);
  for (;;) {
    const candidate = join(dir, "mise.toml");
    if (existsSync(candidate)) {
      return candidate;
    }
    const up = dirname(dir);
    if (up === dir) {
      return null;
    }
    dir = up;
  }
}

/**
 * The major version `mise.toml` asks for, or null if it names none.
 *
 * `node = "24"` is what the file says today and `"24.19.0"` would do as well,
 * so the leading number is what is compared. A pin that is not a version at
 * all -- `lts`, a `ref:` -- is not something to guess at, and answering null
 * makes this quiet rather than wrong.
 */
export function pinnedMajor(toml: string): string | null {
  const line = /^\s*node\s*=\s*"([^"]*)"/m.exec(toml);
  const major = line === null ? null : /^(\d+)/.exec(line[1]);
  return major === null ? null : major[1];
}

/** What to say about this pair, or null when there is nothing to say. */
export function mismatch(pinned: string | null, running: string): string | null {
  if (pinned === null || running.split(".")[0] === pinned) {
    return null;
  }
  return (
    `note: this repository pins node ${pinned} and you are running ${running}. ` +
    "See DEVELOPERS.md#frontend if the suite behaves oddly."
  );
}

/** What there is to say, which is one line or nothing at all. */
export function preflight(from: string, running: string): string | null {
  const path = findMiseToml(from);
  // No pin to compare against is not a fault worth stopping anything for.
  return path === null ? null : mismatch(pinnedMajor(readFileSync(path, "utf8")), running);
}

/** Say it, on the stream a diagnostic belongs on. Nothing when there is nothing. */
export function sayIfMismatched(from: string, running: string): void {
  const said = preflight(from, running);
  if (said !== null) {
    process.stderr.write(`${said}\n`);
  }
}
