import { describe, expect, it } from "vitest";
import { controlsIn, controlsNamed, MOST_WORDS } from "./controls";
import { GUIDES } from "./shots";

/**
 * What counts as a control's name, and what is only emphasis.
 *
 * The browser suite can only check names it was given, so a reader that
 * silently returned nothing would leave `guide-controls.spec.ts` green over an
 * app with no controls at all. Everything here is about the reading; whether
 * the app still has these is that suite's question.
 */

describe("reading the controls out of a guide", () => {
  it("takes a short bold span", () => {
    expect(controlsNamed("Then press **Save**.")).toEqual(["Save"]);
  });

  it("takes one that a wrapped line has split in two", () => {
    expect(controlsNamed("a **Where the\nstock is** list")).toEqual(["Where the stock is"]);
  });

  it("names each control once however often the guide says it", () => {
    expect(controlsNamed("**Save**, and later **Save** again")).toEqual(["Save"]);
  });

  it("leaves a bold sentence alone", () => {
    expect(controlsNamed("**A balance is never stored.**")).toEqual([]);
  });

  it("leaves a bold lead-in alone", () => {
    expect(controlsNamed("**To make one:** add a label.")).toEqual([]);
  });

  it("leaves a bold clause alone", () => {
    expect(controlsNamed("**Search first, always**")).toEqual([]);
  });

  it("leaves a bold phrase longer than a name alone", () => {
    const long = Array(MOST_WORDS + 1)
      .fill("word")
      .join(" ");
    expect(controlsNamed(`**${long}**`)).toEqual([]);
  });

  it("keeps a phrase of exactly that length", () => {
    const name = Array(MOST_WORDS).fill("word").join(" ");
    expect(controlsNamed(`**${name}**`)).toEqual([name]);
  });

  it("keeps two spans on one line apart", () => {
    expect(controlsNamed("**Send now** or **Discard**")).toEqual(["Send now", "Discard"]);
  });
});

describe("the committed guides", () => {
  it("name the controls the volunteer's half is driven by", () => {
    expect(controlsIn("volunteer")).toContain("Save");
  });

  it("name the controls only an administrator has", () => {
    expect(controlsIn("administrator")).toContain("Merged into");
  });

  it("name enough of them that the browser suite is checking something", () => {
    expect(controlsIn("volunteer").length).toBeGreaterThan(5);
    expect(controlsIn("administrator").length).toBeGreaterThan(5);
  });
});

/**
 * What the reading above actually comes out with, committed.
 *
 * The rule that decides this is a heuristic over prose, and the person most
 * likely to move it is the person editing a guide -- who is writing sentences,
 * not code, and whose only signal until now was a browser suite needing a
 * database and two servers before it could answer `names "never"`. A
 * bolded word mid-sentence turns up here as a line added to a text file, in
 * the same diff as the sentence that added it, and `vitest -u` is the whole of
 * accepting it.
 *
 * A file rather than an inline snapshot, because the useful thing is reading
 * the list top to bottom and seeing whether it is a list of controls.
 */
describe("the controls each guide is read as naming", () => {
  it.each(GUIDES)("%s", async (guide) => {
    await expect(`${controlsIn(guide).join("\n")}\n`).toMatchFileSnapshot(
      `./__snapshots__/${guide}.controls.txt`,
    );
  });
});
