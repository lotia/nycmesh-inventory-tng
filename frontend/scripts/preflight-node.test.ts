/**
 * What the preflight says, and the occasions it says nothing.
 *
 * The half worth pinning is the silence: a note printed on every run of a
 * correctly configured machine is one everybody learns to scroll past, and
 * this suite runs on the pinned version far more often than not.
 */
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterAll, describe, expect, it, vi } from "vitest";
import { findMiseToml, mismatch, pinnedMajor, preflight, sayIfMismatched } from "./preflight-node";

/**
 * A directory with no `mise.toml` above it, cleaned up after.
 *
 * `tmpdir()` is above the checkout and holds no `mise.toml`, which is the whole
 * of what these cases need. Removed rather than left, because a suite that
 * leaves two directories behind every run is one that fills a disk slowly
 * enough that nobody connects the two.
 */
const made: string[] = [];

function withoutAMisePin(): string {
  const path = mkdtempSync(join(tmpdir(), "inventory-tng-no-mise-"));
  made.push(path);
  return path;
}

afterAll(() => {
  for (const path of made) {
    rmSync(path, { recursive: true, force: true });
  }
});

describe("finding the file the pin lives in", () => {
  it("walks up from the directory it is given", () => {
    expect(findMiseToml(process.cwd())).toBe(join(process.cwd(), "..", "mise.toml"));
  });

  it("answers nothing rather than guessing when there is none above", () => {
    expect(findMiseToml(withoutAMisePin())).toBeNull();
  });
});

describe("the version mise.toml pins", () => {
  it("is the major, however precisely it is written", () => {
    expect(pinnedMajor('[tools]\nnode = "24"\n')).toBe("24");
    expect(pinnedMajor('[tools]\nnode = "24.19.0"\n')).toBe("24");
  });

  it("is nothing at all when the pin is not a version", () => {
    expect(pinnedMajor('[tools]\nnode = "lts"\n')).toBeNull();
    expect(pinnedMajor('[tools]\npython = "3.14"\n')).toBeNull();
  });
});

describe("what it says about the node in hand", () => {
  it("says nothing when the majors agree, however long the running one is", () => {
    expect(mismatch("24", "24.19.0")).toBeNull();
    expect(mismatch("24", "24.0.0-nightly")).toBeNull();
  });

  it("says nothing when there is no pin to disagree with", () => {
    expect(mismatch(null, "26.7.0")).toBeNull();
  });

  it("names both versions when they differ, so neither has to be looked up", () => {
    const said = mismatch("24", "26.7.0");
    expect(said).toContain("pins node 24");
    expect(said).toContain("running 26.7.0");
  });
});

// End to end, against the pin this repository actually carries, so a version
// bumped in mise.toml is compared against the file rather than against a copy
// of it written here.
describe("the whole of it, against this repository", () => {
  it("is quiet about the node it pins", () => {
    const path = findMiseToml(process.cwd());
    if (path === null) {
      throw new Error("this repository has a mise.toml, and the walk above says where");
    }
    const pinned = pinnedMajor(readFileSync(path, "utf8"));
    expect(pinned).toMatch(/^\d+$/);
    expect(preflight(process.cwd(), `${pinned}.0.0`)).toBeNull();
  });

  it("speaks up about one it does not", () => {
    expect(preflight(process.cwd(), "999.0.0")).toContain("999.0.0");
  });

  it("says nothing at all where there is no mise.toml to read", () => {
    expect(preflight(withoutAMisePin(), "999.0.0")).toBeNull();
  });
});

// What `vite.config.ts` actually calls. A diagnostic goes to stderr, so that
// piping a build's output somewhere does not carry it into whatever reads that.
describe("saying it", () => {
  it("writes one line to stderr when the versions differ", () => {
    const written = vi.spyOn(process.stderr, "write").mockReturnValue(true);
    try {
      sayIfMismatched(process.cwd(), "999.0.0");
      expect(written).toHaveBeenCalledTimes(1);
      const line = String(written.mock.calls[0][0]);
      expect(line).toContain("999.0.0");
      expect(line).toMatch(/\n$/);
    } finally {
      written.mockRestore();
    }
  });

  it("writes nothing at all when there is nothing to say", () => {
    const written = vi.spyOn(process.stderr, "write").mockReturnValue(true);
    try {
      sayIfMismatched(withoutAMisePin(), "999.0.0");
      expect(written).not.toHaveBeenCalled();
    } finally {
      written.mockRestore();
    }
  });
});
