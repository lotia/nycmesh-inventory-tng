import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { type Guide, guidePath, imagePath, SHOTS, shotsFor } from "./shots";

/**
 * The list, the committed PNGs and the guides, held to each other.
 *
 * A picture is the one part of a document that goes wrong silently: a renamed
 * shot leaves a broken image in a file nobody rebuilds, and a shot nothing
 * draws is a binary that lives in the repository for ever. Both are cheap to
 * catch from here.
 */

// From the working directory rather than from `import.meta.url`, which is not
// a file URL under jsdom. Vitest runs with `frontend/` as the root, which is
// where `npm test` is run from and what vite.config.ts is beside.
const REPO_ROOT = `${resolve(process.cwd(), "..")}/`;
const GUIDES: Guide[] = ["volunteer", "administrator"];

const read = (path: string): string => readFileSync(`${REPO_ROOT}${path}`, "utf8");

describe("the shot list", () => {
  it("names each shot once", () => {
    const names = SHOTS.map((shot) => shot.name);
    expect(new Set(names).size).toBe(names.length);
  });

  it("gives every shot to a guide that exists", () => {
    expect(SHOTS.every((shot) => GUIDES.includes(shot.guide))).toBe(true);
  });

  it("draws something for each guide", () => {
    for (const guide of GUIDES) {
      expect(shotsFor(guide).length).toBeGreaterThan(0);
    }
  });
});

describe.each(SHOTS)("$name", ({ name, guide }) => {
  it("is committed", () => {
    expect(existsSync(`${REPO_ROOT}${imagePath(name)}`)).toBe(true);
  });

  it("is drawn by the guide it belongs to", () => {
    expect(read(guidePath(guide))).toContain(`images/${name}.png`);
  });
});
